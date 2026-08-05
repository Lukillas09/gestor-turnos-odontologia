import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from historias.access_policy import registrar_evento_acceso_clinico
from historias.models import AccesoClinicoAuditoria
from pacientes.models import Paciente
from pacientes.normalizacion import normalizar_documento, normalizar_email_para_comparacion
from pacientes.services import asegurar_paciente_asociado_a_odontologo
from turnos.excepciones import (
    obtener_horarios_publicos_disponibles,
    validar_intervalo_reserva_publica,
)
from turnos.integrations.post_commit import (
    EMAIL_CANCELADO,
    EMAIL_CONFIRMADO,
    GOOGLE_ACTUALIZAR,
    GOOGLE_CANCELAR,
    programar_integraciones_turno,
)
from turnos.models import (
    ConfiguracionAgendaInteligente,
    SolicitudTurnoPublica,
    Turno,
    bloquear_agendas_de_turnos,
)
from turnos.notifications import (
    notificar_solicitud_turno_contacto_existente,
    notificar_solicitud_turno_recibida,
)
from turnos.smart_scheduling import (
    ALGORITMO_HORARIO_VERSION,
    buscar_candidato,
    calcular_horarios_inteligentes,
)
from turnos.tipos_turno import aplicar_snapshot_publico, obtener_configuracion_tipo_publica

from .comparaciones import construir_fotografia_solicitud, detectar_diferencias_datos_paciente

logger = logging.getLogger(__name__)

MENSAJE_EMAIL_PUBLICO_REQUERIDO = "Ingresá un email para poder consultar y administrar tu turno."
MENSAJE_EMAIL_PUBLICO_INVALIDO = "Ingresá un email válido."
MENSAJE_MAXIMO_SOLICITUDES_PENDIENTES = (
    "No pudimos registrar otra solicitud en este momento. Consultá tus turnos "
    "o contactá al consultorio."
)


class MaximoSolicitudesPendientesError(Exception):
    mensaje = MENSAJE_MAXIMO_SOLICITUDES_PENDIENTES


@dataclass(frozen=True)
class ResultadoSolicitudTurnoPublica:
    solicitud: SolicitudTurnoPublica
    turno: Turno | None
    paciente: Paciente
    paciente_creado: bool
    requiere_revision: bool
    duplicada: bool = False


def crear_solicitud_publica_de_turno(datos):
    with transaction.atomic():
        documento = normalizar_documento(datos.get("documento"))

        if not documento:
            raise ValidationError({"documento": "Ingresá tu DNI."})

        datos = {
            **datos,
            "documento": documento,
            "email": (datos.get("email") or "").strip(),
        }
        configuracion_tipo, candidato = _validar_reserva_publica_bloqueada(datos)
        datos["configuracion_tipo_turno"] = configuracion_tipo
        datos["candidato_horario"] = candidato

        paciente, paciente_creado = _obtener_o_crear_paciente_publico(datos)

        solicitud_duplicada = _obtener_solicitud_duplicada_exacta(paciente, datos)

        if solicitud_duplicada:
            turno = solicitud_duplicada.turno
            solicitud = solicitud_duplicada
            duplicada = True
        elif paciente.esta_archivado:
            turno = None
            solicitud = _obtener_alerta_administrativa_duplicada(paciente)
            duplicada = solicitud is not None

            if not solicitud:
                solicitud = _crear_fotografia_solicitud(
                    paciente=paciente,
                    turno=None,
                    datos=datos,
                    paciente_existente=True,
                    paciente_archivado=True,
                )
                registrar_evento_acceso_clinico(
                    accion=AccesoClinicoAuditoria.Accion.SOLICITUD_PUBLICA_ARCHIVADO,
                    resultado=AccesoClinicoAuditoria.Resultado.PERMITIDO,
                    politica=AccesoClinicoAuditoria.Politica.PACIENTE_ARCHIVADO,
                    paciente=paciente,
                    motivo="Solicitud publica recibida para paciente archivado.",
                )
        else:
            odontologo = datos["odontologo"]
            _validar_maximo_pendientes_publicos(paciente, datos)
            campos_turno = {
                "paciente": paciente,
                "odontologo": odontologo,
                "fecha": datos["fecha"],
                "hora_inicio": datos["hora_inicio"],
                "motivo": datos.get("motivo", ""),
                "estado": Turno.Estado.PENDIENTE,
            }
            if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
                turno_snapshot = aplicar_snapshot_publico(
                    Turno(**campos_turno),
                    configuracion_tipo,
                    candidato,
                )
                campos_turno.update(
                    {
                        "tipo_turno": turno_snapshot.tipo_turno,
                        "tipo_turno_nombre_snapshot": (turno_snapshot.tipo_turno_nombre_snapshot),
                        "duracion_minutos": turno_snapshot.duracion_minutos,
                        "duracion_atencion_minutos": (turno_snapshot.duracion_atencion_minutos),
                        "margen_posterior_minutos_snapshot": (
                            turno_snapshot.margen_posterior_minutos_snapshot
                        ),
                        "algoritmo_horario_version": (turno_snapshot.algoritmo_horario_version),
                        "clasificacion_horario": turno_snapshot.clasificacion_horario,
                        "puntaje_horario": turno_snapshot.puntaje_horario,
                        "motivo": _construir_motivo_turno(
                            configuracion_tipo.tipo_turno.nombre,
                            datos.get("motivo"),
                        ),
                    }
                )
            else:
                campos_turno.update(
                    {
                        "duracion_minutos": 30,
                        "duracion_atencion_minutos": 30,
                        "margen_posterior_minutos_snapshot": 0,
                        "clasificacion_horario": Turno.ClasificacionHorario.LEGACY,
                    }
                )
            turno = Turno.objects.create(**campos_turno)
            asegurar_paciente_asociado_a_odontologo(
                paciente,
                odontologo,
                motivo="Solicitud publica de turno",
            )
            solicitud = _crear_fotografia_solicitud(
                paciente=paciente,
                turno=turno,
                datos=datos,
                paciente_existente=not paciente_creado,
            )
            transaction.on_commit(
                lambda solicitud_id=solicitud.id: _notificar_solicitud(solicitud_id)
            )
            duplicada = False

    return ResultadoSolicitudTurnoPublica(
        solicitud=solicitud,
        turno=turno,
        paciente=paciente,
        paciente_creado=paciente_creado and not duplicada,
        requiere_revision=solicitud.requiere_revision,
        duplicada=duplicada,
    )


def _validar_reserva_publica_bloqueada(datos):
    odontologo = datos["odontologo"]
    fecha = datos["fecha"]
    hora_inicio = datos["hora_inicio"]

    bloquear_agendas_de_turnos([(odontologo.id, fecha)])

    if not settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
        validar_intervalo_reserva_publica(fecha, hora_inicio, 30)
        horarios = obtener_horarios_publicos_disponibles(
            odontologo=odontologo,
            fecha=fecha,
            duracion_minutos=30,
        )
        if hora_inicio not in horarios:
            raise ValidationError("Ese horario ya no está disponible. Elegí otro horario.")
        return None, None

    configuracion_tipo = obtener_configuracion_tipo_publica(
        odontologo,
        datos.get("tipo_turno"),
        bloquear=True,
    )
    if not configuracion_tipo:
        raise ValidationError(
            {"tipo_turno": "El motivo elegido ya no está disponible para este profesional."}
        )

    configuracion_agenda, _ = ConfiguracionAgendaInteligente.objects.get_or_create(
        odontologo=odontologo
    )
    configuracion_agenda = ConfiguracionAgendaInteligente.objects.select_for_update().get(
        pk=configuracion_agenda.pk
    )
    validar_intervalo_reserva_publica(
        fecha,
        hora_inicio,
        configuracion_tipo.duracion_bloqueada_minutos,
    )
    resultado = calcular_horarios_inteligentes(
        odontologo=odontologo,
        fecha=fecha,
        duracion_atencion_minutos=configuracion_tipo.duracion_atencion_minutos,
        margen_posterior_minutos=configuracion_tipo.margen_posterior_minutos,
        configuracion=configuracion_agenda,
    )
    candidato = buscar_candidato(resultado, hora_inicio)
    if not candidato:
        raise ValidationError("Ese horario ya no está disponible. Elegí otro horario.")
    return configuracion_tipo, candidato


def _construir_motivo_turno(tipo_nombre, comentario):
    comentario = (comentario or "").strip()
    if not comentario:
        return tipo_nombre
    return f"{tipo_nombre} - {comentario}"[:200]


def _obtener_o_crear_paciente_publico(datos):
    documento = datos["documento"]
    paciente = Paciente.objects.select_for_update().filter(documento=documento).first()

    if paciente:
        _validar_email_publico_para_paciente(datos.get("email"), paciente)
        return paciente, False

    _validar_email_publico_para_paciente(datos.get("email"), None)

    try:
        with transaction.atomic():
            return (
                Paciente.objects.create(
                    nombre=datos["nombre"],
                    apellido=datos["apellido"],
                    documento=documento,
                    telefono=datos.get("telefono", ""),
                    email=datos.get("email", ""),
                    origen_alta=Paciente.OrigenAlta.SOLICITUD_PUBLICA,
                    estado_validacion_datos=Paciente.EstadoValidacionDatos.PENDIENTE,
                ),
                True,
            )
    except IntegrityError:
        paciente = Paciente.objects.select_for_update().get(documento=documento)
        _validar_email_publico_para_paciente(datos.get("email"), paciente)
        return paciente, False


def _validar_email_publico_para_paciente(email, paciente):
    email = (email or "").strip()

    if email:
        try:
            validate_email(email)
        except ValidationError as error:
            raise ValidationError({"email": MENSAJE_EMAIL_PUBLICO_INVALIDO}) from error

    if paciente and paciente.esta_archivado:
        return

    email_requerido = paciente is None or not paciente.email

    if email_requerido and not email:
        raise ValidationError({"email": MENSAJE_EMAIL_PUBLICO_REQUERIDO})


def _obtener_solicitud_duplicada_exacta(paciente, datos):
    queryset = (
        SolicitudTurnoPublica.objects.select_related("turno", "paciente")
        .filter(
            paciente=paciente,
            turno__odontologo=datos["odontologo"],
            turno__fecha=datos["fecha"],
            turno__hora_inicio=datos["hora_inicio"],
            turno__estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
        )
        .exclude(estado_revision=SolicitudTurnoPublica.EstadoRevision.RECHAZADA)
    )
    if settings.TURNOS_PUBLIC_SMART_SCHEDULING_ENABLED:
        configuracion_tipo = datos.get("configuracion_tipo_turno")
        queryset = queryset.filter(
            turno__tipo_turno_id=(configuracion_tipo.tipo_turno_id if configuracion_tipo else None)
        )
    return queryset.order_by("-creado_en").first()


def _obtener_alerta_administrativa_duplicada(paciente):
    ventana = settings.TURNOS_PUBLIC_BOOKING_DUPLICATE_WINDOW_SECONDS

    if ventana <= 0:
        return None

    creado_desde = timezone.now() - timedelta(seconds=ventana)
    return (
        SolicitudTurnoPublica.objects.select_for_update()
        .filter(
            paciente=paciente,
            turno__isnull=True,
            estado_revision=SolicitudTurnoPublica.EstadoRevision.PENDIENTE,
            creado_en__gte=creado_desde,
        )
        .order_by("-creado_en")
        .first()
    )


def _validar_maximo_pendientes_publicos(paciente, datos):
    limite = settings.TURNOS_PUBLIC_BOOKING_MAX_PENDING_PER_DNI

    if limite <= 0:
        return

    pendientes = (
        SolicitudTurnoPublica.objects.filter(
            paciente=paciente,
            turno__isnull=False,
            turno__estado=Turno.Estado.PENDIENTE,
            turno__fecha__gte=timezone.localdate(),
        )
        .exclude(estado_revision=SolicitudTurnoPublica.EstadoRevision.RECHAZADA)
        .count()
    )

    if pendientes >= limite:
        logger.warning(
            "Máximo de solicitudes públicas pendientes alcanzado. reason=max_pending",
        )
        raise MaximoSolicitudesPendientesError()


def _crear_fotografia_solicitud(
    paciente,
    turno,
    datos,
    paciente_existente,
    paciente_archivado=False,
):
    fotografia = construir_fotografia_solicitud(datos)
    diferencias = detectar_diferencias_datos_paciente(paciente, datos) if paciente_existente else {}
    requiere_revision = bool(diferencias) or not paciente_existente
    estado_revision = (
        SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        if requiere_revision
        else SolicitudTurnoPublica.EstadoRevision.SIN_DIFERENCIAS
    )

    if paciente_existente and not paciente.email:
        requiere_revision = True
        estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE

    if paciente_archivado:
        requiere_revision = True
        estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        diferencias["estado_operativo"] = {
            "actual": "archivado",
            "enviado": "solicitud_publica",
        }

    snapshot = _construir_snapshot_solicitud(datos)
    return SolicitudTurnoPublica.objects.create(
        paciente=paciente,
        turno=turno,
        paciente_existente=paciente_existente,
        requiere_revision=requiere_revision,
        diferencias_detectadas=diferencias,
        estado_revision=estado_revision,
        notificacion_contacto_existente_error=(
            "Paciente archivado: requiere revision administrativa."
            if paciente_archivado
            else (
                "Paciente existente sin email utilizable."
                if paciente_existente and not paciente.email
                else ""
            )
        ),
        **snapshot,
        **fotografia,
    )


def _construir_snapshot_solicitud(datos):
    configuracion_tipo = datos.get("configuracion_tipo_turno")
    candidato = datos.get("candidato_horario")
    if configuracion_tipo and candidato:
        return {
            "tipo_turno": configuracion_tipo.tipo_turno,
            "tipo_turno_nombre_snapshot": configuracion_tipo.tipo_turno.nombre,
            "duracion_atencion_snapshot": configuracion_tipo.duracion_atencion_minutos,
            "duracion_bloqueada_snapshot": configuracion_tipo.duracion_bloqueada_minutos,
            "margen_posterior_snapshot": configuracion_tipo.margen_posterior_minutos,
            "algoritmo_version": ALGORITMO_HORARIO_VERSION,
            "horario_clasificacion": candidato.clasificacion,
            "horario_puntaje": candidato.puntaje,
        }
    return {
        "duracion_atencion_snapshot": 30,
        "duracion_bloqueada_snapshot": 30,
        "margen_posterior_snapshot": 0,
        "horario_clasificacion": Turno.ClasificacionHorario.LEGACY,
    }


def _notificar_solicitud(solicitud_id):
    solicitud = SolicitudTurnoPublica.objects.select_related(
        "paciente",
        "turno",
        "turno__odontologo",
        "turno__odontologo__usuario",
    ).get(pk=solicitud_id)

    if not solicitud.turno_id:
        return

    if solicitud.paciente_existente:
        _notificar_paciente_existente(solicitud)
        return

    notificar_solicitud_turno_recibida(solicitud.turno)


def _notificar_paciente_existente(solicitud):
    if not solicitud.paciente.email:
        return

    resultado = notificar_solicitud_turno_contacto_existente(solicitud)

    if resultado.enviada:
        solicitud.notificacion_contacto_existente_en = timezone.now()
        solicitud.notificacion_contacto_existente_error = ""
    else:
        solicitud.notificacion_contacto_existente_error = resultado.motivo[:1000]

    solicitud.save(
        update_fields=[
            "notificacion_contacto_existente_en",
            "notificacion_contacto_existente_error",
            "actualizado_en",
        ]
    )


def revisar_solicitud_publica(
    solicitud_id,
    usuario,
    accion,
    campos_a_actualizar=None,
    observaciones="",
):
    campos_a_actualizar = set(campos_a_actualizar or [])

    with transaction.atomic():
        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente")
            .get(pk=solicitud_id)
        )

        paciente = Paciente.objects.select_for_update().get(pk=solicitud.paciente_id)
        _aplicar_revision_solicitud_bloqueada(
            solicitud=solicitud,
            paciente=paciente,
            usuario=usuario,
            accion=accion,
            campos_a_actualizar=campos_a_actualizar,
            observaciones=observaciones,
        )

    return solicitud


def revisar_y_confirmar_solicitud_publica(
    solicitud_id,
    usuario,
    accion,
    campos_a_actualizar=None,
    observaciones="",
    duracion_minutos=30,
):
    campos_a_actualizar = set(campos_a_actualizar or [])

    try:
        duracion = int(duracion_minutos)
    except (TypeError, ValueError) as error:
        raise ValidationError("La duración seleccionada no es válida.") from error

    if duracion <= 0:
        raise ValidationError("La duración debe ser mayor a cero.")

    referencia = _obtener_referencia_solicitud_turno(solicitud_id)
    if not referencia["turno_id"]:
        raise ValidationError("Esta solicitud no tiene un turno asociado.")

    with transaction.atomic():
        bloquear_agendas_de_turnos([(referencia["odontologo_id"], referencia["fecha"])])
        turno = (
            Turno.objects.select_for_update()
            .select_related("paciente", "odontologo", "odontologo__usuario")
            .get(pk=referencia["turno_id"])
        )
        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente", "turno", "turno__odontologo", "turno__odontologo__usuario")
            .get(pk=solicitud_id)
        )

        if (
            solicitud.turno_id != turno.pk
            or turno.odontologo_id != referencia["odontologo_id"]
            or turno.fecha != referencia["fecha"]
        ):
            raise ValidationError("La solicitud cambió mientras se procesaba. Volvé a intentar.")
        paciente = Paciente.objects.select_for_update().get(pk=solicitud.paciente_id)

        if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue revisada.")

        if turno.estado != Turno.Estado.PENDIENTE:
            raise ValidationError("Solo se pueden confirmar turnos pendientes.")

        _aplicar_revision_solicitud_bloqueada(
            solicitud=solicitud,
            paciente=paciente,
            usuario=usuario,
            accion=accion,
            campos_a_actualizar=campos_a_actualizar,
            observaciones=observaciones,
        )

        update_fields = ["duracion_minutos", "estado", "actualizado_en"]
        if turno.duracion_minutos != duracion and (
            turno.tipo_turno_id or turno.duracion_atencion_minutos is not None
        ):
            margen = turno.margen_posterior_minutos_snapshot
            if margen >= duracion:
                margen = 0
            turno.duracion_atencion_minutos = duracion - margen
            turno.margen_posterior_minutos_snapshot = margen
            turno.algoritmo_horario_version = ""
            turno.clasificacion_horario = Turno.ClasificacionHorario.INTERNO
            turno.puntaje_horario = None
            update_fields.extend(
                [
                    "duracion_atencion_minutos",
                    "margen_posterior_minutos_snapshot",
                    "algoritmo_horario_version",
                    "clasificacion_horario",
                    "puntaje_horario",
                ]
            )
        turno.duracion_minutos = duracion
        turno.estado = Turno.Estado.CONFIRMADO
        turno.save(update_fields=update_fields, _agenda_ya_bloqueada=True)

    programar_integraciones_turno(
        turno.pk,
        google=GOOGLE_ACTUALIZAR,
        email=EMAIL_CONFIRMADO,
    )
    return turno, solicitud


def rechazar_solicitud_publica_y_cancelar_turno(solicitud_id, usuario, motivo):
    motivo = (motivo or "").strip()

    if not motivo:
        raise ValidationError("Indicá un motivo para rechazar la solicitud.")

    referencia = _obtener_referencia_solicitud_turno(solicitud_id)

    with transaction.atomic():
        turno = None
        if referencia["turno_id"]:
            bloquear_agendas_de_turnos([(referencia["odontologo_id"], referencia["fecha"])])
            turno = Turno.objects.select_for_update().get(pk=referencia["turno_id"])

        solicitud = (
            SolicitudTurnoPublica.objects.select_for_update()
            .select_related("paciente", "turno", "turno__odontologo")
            .get(pk=solicitud_id)
        )

        if solicitud.turno_id != referencia["turno_id"] or (
            turno
            and (
                turno.odontologo_id != referencia["odontologo_id"]
                or turno.fecha != referencia["fecha"]
            )
        ):
            raise ValidationError("La solicitud cambió mientras se procesaba. Volvé a intentar.")

        if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
            raise ValidationError("Esta solicitud ya fue revisada.")

        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
        solicitud.requiere_revision = False
        solicitud.revisada_por = usuario
        solicitud.revisada_en = timezone.now()
        solicitud.observaciones_revision = motivo
        solicitud.campos_actualizados = []
        solicitud.campos_descartados = sorted(
            set((solicitud.diferencias_detectadas or {}).keys()) & _campos_actualizables_revision()
        )
        solicitud.save(
            update_fields=[
                "estado_revision",
                "requiere_revision",
                "revisada_por",
                "revisada_en",
                "observaciones_revision",
                "campos_actualizados",
                "campos_descartados",
                "actualizado_en",
            ]
        )

        if turno:
            if turno.estado != Turno.Estado.CANCELADO:
                turno.estado = Turno.Estado.CANCELADO
                turno.motivo_cancelacion_paciente = motivo
                turno.save(
                    update_fields=["estado", "motivo_cancelacion_paciente", "actualizado_en"],
                    _agenda_ya_bloqueada=True,
                )

            from turnos.public_access.services import revocar_acciones_publicas_de_turno

            revocar_acciones_publicas_de_turno(turno)

    if turno:
        programar_integraciones_turno(
            turno.pk,
            google=GOOGLE_CANCELAR,
            email=EMAIL_CANCELADO,
        )

    return solicitud


def _obtener_referencia_solicitud_turno(solicitud_id):
    referencia = SolicitudTurnoPublica.objects.values(
        "turno_id",
        "turno__odontologo_id",
        "turno__fecha",
    ).get(pk=solicitud_id)
    return {
        "turno_id": referencia["turno_id"],
        "odontologo_id": referencia["turno__odontologo_id"],
        "fecha": referencia["turno__fecha"],
    }


def cerrar_revision_por_cancelacion_de_turno(turno, usuario=None, motivo=""):
    try:
        solicitud = turno.solicitud_publica
    except SolicitudTurnoPublica.DoesNotExist:
        return None

    if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
        return solicitud

    solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
    solicitud.requiere_revision = False
    solicitud.revisada_por = usuario if getattr(usuario, "is_authenticated", False) else None
    solicitud.revisada_en = timezone.now()
    solicitud.observaciones_revision = (
        motivo or "Turno cancelado antes de revisar la solicitud."
    ).strip()
    solicitud.campos_actualizados = []
    solicitud.campos_descartados = sorted(
        set((solicitud.diferencias_detectadas or {}).keys()) & _campos_actualizables_revision()
    )
    solicitud.save(
        update_fields=[
            "estado_revision",
            "requiere_revision",
            "revisada_por",
            "revisada_en",
            "observaciones_revision",
            "campos_actualizados",
            "campos_descartados",
            "actualizado_en",
        ]
    )
    return solicitud


def _aplicar_revision_solicitud_bloqueada(
    solicitud,
    paciente,
    usuario,
    accion,
    campos_a_actualizar,
    observaciones="",
):
    if solicitud.estado_revision != SolicitudTurnoPublica.EstadoRevision.PENDIENTE:
        raise ValidationError("Esta solicitud ya fue revisada.")

    if not paciente.activo and accion not in {"mantener_pendiente", "rechazar"}:
        raise ValidationError(
            "El paciente esta archivado. Reactivalo manualmente antes de validar o modificar datos."
        )

    campos_validos = _campos_actualizables_revision()
    campos_con_diferencias = set((solicitud.diferencias_detectadas or {}).keys()) & campos_validos
    campos_a_actualizar = set(campos_a_actualizar or [])

    if accion == "mantener_pendiente":
        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.PENDIENTE
        solicitud.requiere_revision = True
        solicitud.observaciones_revision = observaciones.strip()
        solicitud.save(
            update_fields=[
                "estado_revision",
                "requiere_revision",
                "observaciones_revision",
                "actualizado_en",
            ]
        )
        return solicitud

    campos_no_permitidos = campos_a_actualizar - campos_validos
    if campos_no_permitidos:
        raise ValidationError("La selección de campos a actualizar no es válida.")

    if accion == "aplicar_campos":
        campos_sin_diferencias = campos_a_actualizar - campos_con_diferencias
        if campos_sin_diferencias:
            raise ValidationError("Solo se pueden aplicar campos con diferencias detectadas.")

    campos_actualizados = []
    campos_guardado_paciente = set()

    if accion == "aplicar_campos":
        for campo in campos_validos & campos_a_actualizar:
            valor_enviado = getattr(solicitud, f"{campo}_enviado")

            if campo == "email":
                email_enviado = (valor_enviado or "").strip()
                email_actual_normalizado = normalizar_email_para_comparacion(paciente.email)
                email_enviado_normalizado = normalizar_email_para_comparacion(email_enviado)

                if email_actual_normalizado == email_enviado_normalizado:
                    continue

                paciente.email = email_enviado
                paciente.email_verificado_en = None
                campos_guardado_paciente.update({"email", "email_verificado_en"})
                campos_actualizados.append(campo)
                continue

            setattr(paciente, campo, valor_enviado)
            campos_guardado_paciente.add(campo)
            campos_actualizados.append(campo)

        if campos_guardado_paciente:
            paciente.save(update_fields=[*sorted(campos_guardado_paciente), "actualizado_en"])
            solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.CAMBIOS_APLICADOS
        else:
            solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS
    elif accion == "validar_paciente":
        paciente.estado_validacion_datos = Paciente.EstadoValidacionDatos.VALIDADO
        paciente.validado_por = usuario
        paciente.validado_en = timezone.now()
        paciente.save(
            update_fields=[
                "estado_validacion_datos",
                "validado_por",
                "validado_en",
                "actualizado_en",
            ]
        )
        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS
    elif accion == "rechazar":
        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.RECHAZADA
    else:
        solicitud.estado_revision = SolicitudTurnoPublica.EstadoRevision.REVISADA_SIN_CAMBIOS

    solicitud.requiere_revision = False
    solicitud.revisada_por = usuario
    solicitud.revisada_en = timezone.now()
    solicitud.observaciones_revision = observaciones.strip()
    solicitud.campos_actualizados = sorted(campos_actualizados)
    solicitud.campos_descartados = sorted(campos_con_diferencias - set(campos_actualizados))
    solicitud.save(
        update_fields=[
            "estado_revision",
            "requiere_revision",
            "revisada_por",
            "revisada_en",
            "observaciones_revision",
            "campos_actualizados",
            "campos_descartados",
            "actualizado_en",
        ]
    )
    return solicitud


def _campos_actualizables_revision():
    return {"nombre", "apellido", "telefono", "email"}
