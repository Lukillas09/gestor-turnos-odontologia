import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from historias.access_policy import registrar_evento_acceso_clinico
from historias.models import AccesoClinicoAuditoria
from pacientes.models import Paciente
from turnos.excepciones import (
    obtener_horarios_publicos_disponibles,
    validar_intervalo_reserva_publica,
)
from turnos.models import bloquear_agendas_de_turnos
from turnos.notifications import notificar_codigo_acceso_publico_turnos
from turnos.services import cancelar_turno, reprogramar_turno
from turnos.smart_scheduling import buscar_candidato, calcular_horarios_inteligentes

from ..models import (
    AccionPublicaTurno,
    ConfiguracionAgendaInteligente,
    DesafioAccesoPublicoTurnos,
    Turno,
)
from .rate_limit import incrementar_limite
from .tokens import (
    PUBLIC_ACCESS_PENDING_CHALLENGE_KEY,
    PUBLIC_ACCESS_SESSION_KEY,
    PUBLIC_ACTION_TOKENS_SESSION_KEY,
    generar_codigo_otp,
    generar_token_accion,
    hash_valor_publico,
    normalizar_documento,
    obtener_ip_cliente,
)

logger = logging.getLogger(__name__)

MENSAJE_SOLICITUD_GENERICA = (
    "Revisá tu medio de contacto. Si los datos ingresados coinciden con un paciente "
    "registrado, recibirás un código para continuar."
)
MENSAJE_CODIGO_INVALIDO = "El código no es válido o ya venció. Revisalo o solicitá uno nuevo."
MENSAJE_ACCION_INVALIDA = "La acción ya no es válida. Volvé a consultar tus turnos."


@dataclass(frozen=True)
class ResultadoSolicitudAcceso:
    desafio_id: str
    mensaje: str = MENSAJE_SOLICITUD_GENERICA
    limitado: bool = False
    requiere_turnstile: bool = False


@dataclass(frozen=True)
class ResultadoValidacionOTP:
    valido: bool
    mensaje: str = MENSAJE_CODIGO_INVALIDO
    paciente_id: int | None = None
    desafio_id: str | None = None


def registrar_intento_solicitud_acceso(request, documento):
    documento = normalizar_documento(documento)
    ip_hash = hash_valor_publico(obtener_ip_cliente(request), "ip")
    dni_hash = hash_valor_publico(documento, "dni")
    limite_ip = incrementar_limite(
        "solicitud_ip",
        ip_hash,
        settings.TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT,
        settings.TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS,
    )
    limite_dni = incrementar_limite(
        "solicitud_dni",
        dni_hash,
        settings.TURNOS_PUBLIC_ACCESS_REQUEST_LIMIT,
        settings.TURNOS_PUBLIC_ACCESS_REQUEST_WINDOW_SECONDS,
    )
    return limite_ip, limite_dni


def solicitar_acceso_publico_turnos(request, documento, *, limites=None):
    documento = normalizar_documento(documento)
    ip_hash = hash_valor_publico(obtener_ip_cliente(request), "ip")
    dni_hash = hash_valor_publico(documento, "dni")
    limite_ip, limite_dni = limites or registrar_intento_solicitud_acceso(request, documento)

    desafio = _crear_desafio(documento, ip_hash, dni_hash)
    request.session[PUBLIC_ACCESS_PENDING_CHALLENGE_KEY] = str(desafio.id)
    request.session.modified = True

    if not limite_ip.permitido or not limite_dni.permitido:
        return ResultadoSolicitudAcceso(str(desafio.id), limitado=True)

    _enviar_codigo_si_corresponde(desafio)
    return ResultadoSolicitudAcceso(str(desafio.id))


def _crear_desafio(documento, ip_hash, dni_hash):
    paciente = Paciente.objects.filter(documento=documento).first() if documento else None
    canal = DesafioAccesoPublicoTurnos.Canal.EMAIL

    if paciente and not paciente.activo:
        registrar_evento_acceso_clinico(
            accion=AccesoClinicoAuditoria.Accion.OTP_ARCHIVADO,
            resultado=AccesoClinicoAuditoria.Resultado.DENEGADO,
            politica=AccesoClinicoAuditoria.Politica.PACIENTE_ARCHIVADO,
            paciente=paciente,
            motivo="Solicitud de OTP publico para paciente archivado.",
        )
        paciente = None
        canal = DesafioAccesoPublicoTurnos.Canal.FICTICIO

    if not paciente or not paciente.email:
        paciente = None
        canal = DesafioAccesoPublicoTurnos.Canal.FICTICIO

    ahora = timezone.now()

    if paciente:
        DesafioAccesoPublicoTurnos.objects.filter(
            paciente=paciente,
            validado_en__isnull=True,
            invalidado_en__isnull=True,
        ).update(invalidado_en=ahora)

    codigo = generar_codigo_otp()
    return DesafioAccesoPublicoTurnos.objects.create(
        paciente=paciente,
        canal=canal,
        codigo_hash=make_password(codigo),
        expira_en=ahora + timedelta(seconds=settings.TURNOS_PUBLIC_OTP_SECONDS),
        cantidad_envios=0,
        ip_hash=ip_hash,
        dni_hash=dni_hash,
    )


def _enviar_codigo_si_corresponde(desafio):
    if desafio.canal != DesafioAccesoPublicoTurnos.Canal.EMAIL or not desafio.paciente_id:
        return

    codigo = _regenerar_codigo(desafio)
    resultado = notificar_codigo_acceso_publico_turnos(
        paciente=desafio.paciente,
        codigo=codigo,
        expira_en=desafio.expira_en,
    )

    ahora = timezone.now()
    desafio.cantidad_envios += 1
    desafio.ultimo_envio_en = ahora
    desafio.save(update_fields=["cantidad_envios", "ultimo_envio_en"])

    if not resultado.enviada:
        logger.warning(
            "No se pudo enviar OTP de acceso público. desafio_id=%s motivo=%s",
            desafio.id,
            resultado.motivo,
        )


def _regenerar_codigo(desafio):
    codigo = generar_codigo_otp()
    desafio.codigo_hash = make_password(codigo)
    desafio.save(update_fields=["codigo_hash"])
    return codigo


def reenviar_codigo_acceso_publico(request):
    desafio = obtener_desafio_pendiente(request)

    if not desafio:
        return MENSAJE_SOLICITUD_GENERICA

    ip_hash = hash_valor_publico(obtener_ip_cliente(request), "ip")
    limite_ip = incrementar_limite(
        "reenvio_ip",
        ip_hash,
        settings.TURNOS_PUBLIC_RESEND_LIMIT,
        settings.TURNOS_PUBLIC_RESEND_WINDOW_SECONDS,
    )
    limite_dni = incrementar_limite(
        "reenvio_dni",
        desafio.dni_hash or hash_valor_publico("sin-dni", "dni"),
        settings.TURNOS_PUBLIC_RESEND_LIMIT,
        settings.TURNOS_PUBLIC_RESEND_WINDOW_SECONDS,
    )

    ahora = timezone.now()
    en_cooldown = desafio.ultimo_envio_en and ahora - desafio.ultimo_envio_en < timedelta(
        seconds=settings.TURNOS_PUBLIC_RESEND_SECONDS
    )

    if not limite_ip.permitido or not limite_dni.permitido or en_cooldown:
        return MENSAJE_SOLICITUD_GENERICA

    _enviar_codigo_si_corresponde(desafio)
    return MENSAJE_SOLICITUD_GENERICA


def obtener_desafio_pendiente(request):
    desafio_id = request.session.get(PUBLIC_ACCESS_PENDING_CHALLENGE_KEY)

    if not desafio_id:
        return None

    try:
        return DesafioAccesoPublicoTurnos.objects.select_related("paciente").get(pk=desafio_id)
    except (DesafioAccesoPublicoTurnos.DoesNotExist, ValueError, TypeError):
        return None


def validar_codigo_acceso_publico(request, codigo):
    desafio = obtener_desafio_pendiente(request)

    if not desafio or not desafio.esta_activo:
        return ResultadoValidacionOTP(False)

    if desafio.intentos_fallidos >= settings.TURNOS_PUBLIC_OTP_ATTEMPTS:
        desafio.invalidar()
        desafio.save(update_fields=["invalidado_en"])
        return ResultadoValidacionOTP(False)

    if not check_password(codigo, desafio.codigo_hash):
        desafio.intentos_fallidos += 1

        if desafio.intentos_fallidos >= settings.TURNOS_PUBLIC_OTP_ATTEMPTS:
            desafio.invalidar()
            desafio.save(update_fields=["intentos_fallidos", "invalidado_en"])
        else:
            desafio.save(update_fields=["intentos_fallidos"])

        return ResultadoValidacionOTP(False)

    if not desafio.paciente_id or not desafio.paciente.activo:
        desafio.invalidar()
        desafio.save(update_fields=["invalidado_en"])
        return ResultadoValidacionOTP(False)

    ahora = timezone.now()
    desafio.validado_en = ahora
    desafio.save(update_fields=["validado_en"])

    paciente = desafio.paciente

    if paciente.email and paciente.email_verificado_en is None:
        paciente.email_verificado_en = ahora
        paciente.save(update_fields=["email_verificado_en", "actualizado_en"])

    request.session.cycle_key()
    request.session[PUBLIC_ACCESS_SESSION_KEY] = {
        "paciente_id": desafio.paciente_id,
        "desafio_id": str(desafio.id),
        "validado_en": ahora.isoformat(),
    }
    request.session.pop(PUBLIC_ACCESS_PENDING_CHALLENGE_KEY, None)
    request.session.pop(PUBLIC_ACTION_TOKENS_SESSION_KEY, None)
    request.session.set_expiry(settings.TURNOS_PUBLIC_SESSION_SECONDS)
    request.session.modified = True

    return ResultadoValidacionOTP(True, paciente_id=desafio.paciente_id, desafio_id=str(desafio.id))


def cerrar_acceso_publico(request):
    request.session.pop(PUBLIC_ACCESS_SESSION_KEY, None)
    request.session.pop(PUBLIC_ACCESS_PENDING_CHALLENGE_KEY, None)
    request.session.pop(PUBLIC_ACTION_TOKENS_SESSION_KEY, None)
    request.session.modified = True


def generar_permisos_para_turnos(request, paciente_id, turnos):
    ahora = timezone.now()
    acciones_por_turno: dict[int, dict[str, AccionPublicaTurno]] = {}
    tokens_session = {}

    for turno in turnos:
        acciones_por_turno[turno.pk] = {}

        for tipo_accion in _tipos_accion_para_turno(turno):
            AccionPublicaTurno.objects.filter(
                paciente_id=paciente_id,
                turno=turno,
                tipo_accion=tipo_accion,
                utilizado_en__isnull=True,
                revocado_en__isnull=True,
            ).update(revocado_en=ahora)

            token = generar_token_accion()
            accion = AccionPublicaTurno.objects.create(
                paciente_id=paciente_id,
                turno=turno,
                tipo_accion=tipo_accion,
                token_hash=make_password(token),
                version_turno=turno.version_publica,
                expira_en=ahora + timedelta(seconds=settings.TURNOS_PUBLIC_ACTION_TOKEN_SECONDS),
            )
            acciones_por_turno[turno.pk][tipo_accion] = accion
            tokens_session[str(accion.id)] = token

    request.session[PUBLIC_ACTION_TOKENS_SESSION_KEY] = tokens_session
    request.session.modified = True
    return acciones_por_turno


def _tipos_accion_para_turno(turno):
    acciones = [AccionPublicaTurno.TipoAccion.CANCELAR]

    if turno.estado == Turno.Estado.PENDIENTE:
        acciones.append(AccionPublicaTurno.TipoAccion.REPROGRAMAR)

    return acciones


def obtener_token_accion_desde_session(request, accion_id):
    return request.session.get(PUBLIC_ACTION_TOKENS_SESSION_KEY, {}).get(str(accion_id), "")


def validar_accion_publica_sin_consumir(accion_id, token, paciente_id, tipo_accion):
    try:
        accion = AccionPublicaTurno.objects.select_related("turno", "paciente").get(pk=accion_id)
    except (AccionPublicaTurno.DoesNotExist, ValueError, TypeError, ValidationError):
        return None

    if not _accion_publica_valida(accion, token, paciente_id, tipo_accion):
        return None

    return accion


def cancelar_turno_publico_seguro(accion_id, token, paciente_id, motivo_cancelacion):
    with transaction.atomic():
        accion = _obtener_accion_bloqueada(accion_id)

        if not _accion_publica_valida(
            accion,
            token,
            paciente_id,
            AccionPublicaTurno.TipoAccion.CANCELAR,
        ):
            return False

        turno = Turno.objects.select_for_update().get(pk=accion.turno_id)

        if turno.estado not in [Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO]:
            return False

        cancelar_turno(turno, motivo_cancelacion_paciente=motivo_cancelacion)
        _consumir_accion(accion)
        revocar_acciones_publicas_de_turno(turno)
        return True


def reprogramar_turno_publico_seguro(accion_id, token, paciente_id, datos):
    with transaction.atomic():
        accion = _obtener_accion_bloqueada(accion_id)

        if not _accion_publica_valida(
            accion,
            token,
            paciente_id,
            AccionPublicaTurno.TipoAccion.REPROGRAMAR,
        ):
            return False, None

        turno = Turno.objects.select_for_update().get(pk=accion.turno_id)

        if turno.estado != Turno.Estado.PENDIENTE:
            return False, None

        datos = {**datos, "duracion_minutos": turno.duracion_minutos}
        bloquear_agendas_de_turnos(
            [
                (turno.odontologo_id, turno.fecha),
                (turno.odontologo_id, datos["fecha"]),
            ]
        )
        validar_intervalo_reserva_publica(
            datos["fecha"],
            datos["hora_inicio"],
            turno.duracion_minutos,
        )
        if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED and turno.tipo_turno_id:
            configuracion_agenda, _ = ConfiguracionAgendaInteligente.objects.get_or_create(
                odontologo=turno.odontologo
            )
            configuracion_agenda = ConfiguracionAgendaInteligente.objects.select_for_update().get(
                pk=configuracion_agenda.pk
            )
            resultado = calcular_horarios_inteligentes(
                odontologo=turno.odontologo,
                fecha=datos["fecha"],
                duracion_atencion_minutos=(
                    turno.duracion_atencion_minutos or turno.duracion_minutos
                ),
                margen_posterior_minutos=turno.margen_posterior_minutos_snapshot,
                turno_excluido=turno,
                configuracion=configuracion_agenda,
            )
            candidato = buscar_candidato(resultado, datos["hora_inicio"])
            if not candidato:
                raise ValidationError("Ese horario ya no está disponible. Elegí otro horario.")
            datos.update(
                {
                    "algoritmo_horario_version": resultado.algoritmo_version,
                    "clasificacion_horario": candidato.clasificacion,
                    "puntaje_horario": candidato.puntaje,
                }
            )
        else:
            horarios = obtener_horarios_publicos_disponibles(
                odontologo=turno.odontologo,
                fecha=datos["fecha"],
                duracion_minutos=turno.duracion_minutos,
                turno_excluido=turno,
            )
            if datos["hora_inicio"] not in horarios:
                raise ValidationError("Ese horario ya no está disponible. Elegí otro horario.")

        turno = reprogramar_turno(turno, datos)
        _consumir_accion(accion)
        revocar_acciones_publicas_de_turno(turno)
        return True, turno


def _obtener_accion_bloqueada(accion_id):
    try:
        return (
            AccionPublicaTurno.objects.select_for_update().select_related("turno").get(pk=accion_id)
        )
    except (AccionPublicaTurno.DoesNotExist, ValueError, TypeError):
        return None


def _accion_publica_valida(accion, token, paciente_id, tipo_accion):
    if accion is None or not token:
        return False

    if not accion.esta_activa:
        return False

    if accion.paciente_id != paciente_id or accion.tipo_accion != tipo_accion:
        return False

    if not accion.paciente.activo:
        return False

    if accion.turno.paciente_id != paciente_id:
        return False

    if accion.turno.version_publica != accion.version_turno:
        return False

    return check_password(token, accion.token_hash)


def _consumir_accion(accion):
    accion.utilizado_en = timezone.now()
    accion.save(update_fields=["utilizado_en"])


def revocar_acciones_publicas_de_turno(turno):
    AccionPublicaTurno.objects.filter(
        turno=turno,
        utilizado_en__isnull=True,
        revocado_en__isnull=True,
    ).update(revocado_en=timezone.now())


def obtener_uuid(valor):
    try:
        return UUID(str(valor))
    except (TypeError, ValueError):
        return None
