from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from pacientes.models import Paciente
from pacientes.services import asegurar_paciente_asociado_a_odontologo

from .google_calendar_sync import (
    sincronizar_turno_actualizado,
    sincronizar_turno_cancelado,
    sincronizar_turno_creado,
)
from .models import Turno, bloquear_agendas_de_turnos
from .notifications import (
    notificar_recordatorio_turno,
    notificar_solicitud_turno_recibida,
    notificar_turno_cancelado,
    notificar_turno_confirmado,
    notificar_turno_reprogramado,
)
from .selectors import obtener_turno_superpuesto


@dataclass(frozen=True)
class ResultadoEnvioRecordatoriosEmail:
    encontrados: int
    enviados: int
    fallidos: int


@dataclass(frozen=True)
class ResultadoConfirmacionTurno:
    confirmado: bool
    turno: Turno
    conflicto: Turno | None = None
    mensaje: str = ""


def crear_turno_desde_formulario(form, usuario=None):
    form.instance.estado = Turno.Estado.CONFIRMADO
    turno = form.save()
    asegurar_paciente_asociado_a_odontologo(
        turno.paciente,
        turno.odontologo,
        usuario=usuario,
        motivo="Turno creado desde panel interno",
    )
    sincronizar_turno_creado(turno)
    return turno


def actualizar_turno_desde_formulario(form, usuario=None):
    turno = form.save()
    asegurar_paciente_asociado_a_odontologo(
        turno.paciente,
        turno.odontologo,
        usuario=usuario,
        motivo="Turno actualizado desde panel interno",
    )
    sincronizar_turno_actualizado(turno)
    return turno


def reintentar_sincronizacion_google_calendar(turno):
    return sincronizar_turno_actualizado(turno)


def reprogramar_turno(turno, datos):
    turno.fecha = datos["fecha"]
    turno.hora_inicio = datos["hora_inicio"]
    turno.duracion_minutos = datos["duracion_minutos"]
    turno.save(update_fields=["fecha", "hora_inicio", "duracion_minutos", "actualizado_en"])
    sincronizar_turno_actualizado(turno)
    notificar_turno_reprogramado(turno)
    return turno


def confirmar_turno(turno):
    return confirmar_turno_con_duracion(turno, turno.duracion_minutos)


def confirmar_turno_con_duracion(turno, duracion_minutos):
    try:
        duracion = int(duracion_minutos)
    except (TypeError, ValueError):
        return ResultadoConfirmacionTurno(
            confirmado=False,
            turno=turno,
            mensaje="La duración seleccionada no es válida.",
        )

    if duracion <= 0:
        return ResultadoConfirmacionTurno(
            confirmado=False,
            turno=turno,
            mensaje="La duración debe ser mayor a cero.",
        )

    try:
        with transaction.atomic():
            bloquear_agendas_de_turnos([(turno.odontologo_id, turno.fecha)])
            turno = (
                Turno.objects.select_for_update()
                .select_related("paciente", "odontologo", "odontologo__usuario")
                .get(pk=turno.pk)
            )

            if turno.estado != Turno.Estado.PENDIENTE:
                return ResultadoConfirmacionTurno(
                    confirmado=False,
                    turno=turno,
                    mensaje="Solo se pueden confirmar turnos pendientes.",
                )

            conflicto = obtener_turno_superpuesto(
                odontologo=turno.odontologo,
                fecha=turno.fecha,
                hora_inicio=turno.hora_inicio,
                duracion_minutos=duracion,
                turno_excluido=turno,
            )

            if conflicto:
                return ResultadoConfirmacionTurno(
                    confirmado=False,
                    turno=turno,
                    conflicto=conflicto,
                    mensaje=(
                        "No se puede confirmar este turno con esa duración porque "
                        "se superpone con otro turno."
                    ),
                )

            turno.duracion_minutos = duracion
            turno.estado = Turno.Estado.CONFIRMADO
            turno.save(update_fields=["duracion_minutos", "estado", "actualizado_en"])
    except ValidationError as error:
        return ResultadoConfirmacionTurno(
            confirmado=False,
            turno=turno,
            mensaje=_mensaje_validacion(error),
        )

    sincronizar_turno_actualizado(turno)
    notificar_turno_confirmado(turno)
    return ResultadoConfirmacionTurno(
        confirmado=True,
        turno=turno,
        mensaje="Turno confirmado correctamente.",
    )


def cancelar_turno(turno, motivo_cancelacion_paciente=""):
    if turno.estado == Turno.Estado.CANCELADO:
        return turno

    turno.estado = Turno.Estado.CANCELADO

    update_fields = ["estado", "actualizado_en"]

    if motivo_cancelacion_paciente is not None:
        turno.motivo_cancelacion_paciente = motivo_cancelacion_paciente.strip()
        update_fields.append("motivo_cancelacion_paciente")

    turno.save(update_fields=update_fields)
    sincronizar_turno_cancelado(turno)
    notificar_turno_cancelado(turno)
    return turno


def enviar_recordatorios_email(horas_anticipacion=None, ahora=None, fail_silently=True):
    turnos = obtener_turnos_para_recordatorio(
        horas_anticipacion=horas_anticipacion,
        ahora=ahora,
    )
    enviados = 0
    fallidos = 0

    for turno in turnos:
        resultado = notificar_recordatorio_turno(turno, fail_silently=fail_silently)

        if resultado.enviada:
            turno.recordatorio_email_enviado_en = timezone.now()
            turno.recordatorio_email_ultimo_error = ""
            turno.save(
                update_fields=[
                    "recordatorio_email_enviado_en",
                    "recordatorio_email_ultimo_error",
                    "actualizado_en",
                ]
            )
            enviados += 1
            continue

        turno.recordatorio_email_ultimo_error = resultado.motivo[:1000]
        turno.save(update_fields=["recordatorio_email_ultimo_error", "actualizado_en"])
        fallidos += 1

    return ResultadoEnvioRecordatoriosEmail(
        encontrados=len(turnos),
        enviados=enviados,
        fallidos=fallidos,
    )


def obtener_turnos_para_recordatorio(horas_anticipacion=None, ahora=None):
    if horas_anticipacion is None:
        horas_anticipacion = settings.TURNOS_RECORDATORIO_HORAS

    _validar_horas_anticipacion(horas_anticipacion)

    ahora = _normalizar_momento(ahora or timezone.now())
    limite = ahora + timedelta(hours=horas_anticipacion)

    turnos_candidatos = Turno.objects.select_related(
        "paciente",
        "odontologo",
        "odontologo__usuario",
    ).filter(
        estado=Turno.Estado.CONFIRMADO,
        recordatorio_email_enviado_en__isnull=True,
        paciente__email__gt="",
        fecha__gte=timezone.localdate(ahora),
        fecha__lte=timezone.localdate(limite),
    )

    return [
        turno
        for turno in turnos_candidatos
        if ahora <= turno.fecha_hora_inicio_local <= limite
    ]


def _validar_horas_anticipacion(horas_anticipacion):
    if horas_anticipacion <= 0:
        raise ValueError("La anticipacion del recordatorio debe ser mayor a cero.")


def _normalizar_momento(momento):
    zona_horaria = timezone.get_current_timezone()

    if timezone.is_naive(momento):
        return timezone.make_aware(momento, zona_horaria)

    return timezone.localtime(momento, zona_horaria)


def crear_solicitud_turno_publica(datos):
    paciente = obtener_o_crear_paciente_desde_solicitud(datos)
    odontologo = datos["odontologo"]

    turno = Turno.objects.create(
        paciente=paciente,
        odontologo=odontologo,
        fecha=datos["fecha"],
        hora_inicio=datos["hora_inicio"],
        duracion_minutos=30,
        motivo=datos["motivo"],
        estado=Turno.Estado.PENDIENTE,
    )
    asegurar_paciente_asociado_a_odontologo(
        paciente,
        odontologo,
        motivo="Solicitud pública de turno",
    )
    notificar_solicitud_turno_recibida(turno)
    return turno


def _mensaje_validacion(error):
    if hasattr(error, "message_dict"):
        mensajes = []
        for errores in error.message_dict.values():
            mensajes.extend(str(mensaje) for mensaje in errores)
        return " ".join(mensajes)

    return " ".join(str(mensaje) for mensaje in getattr(error, "messages", [error]))


def obtener_o_crear_paciente_desde_solicitud(datos):
    documento = datos.get("documento")
    email = datos.get("email", "")
    paciente = None
    datos_paciente = {
        "nombre": datos["nombre"],
        "apellido": datos["apellido"],
        "telefono": datos["telefono"],
    }

    if documento:
        paciente = Paciente.objects.filter(documento=documento).first()
        datos_paciente["documento"] = documento

    if email:
        datos_paciente["email"] = email

    if not paciente:
        return Paciente.objects.create(
            **{
                **datos_paciente,
                "documento": documento,
                "email": email,
            }
        )

    for campo, valor in datos_paciente.items():
        setattr(paciente, campo, valor)

    paciente.save(
        update_fields=[
            *datos_paciente.keys(),
            "actualizado_en",
        ]
    )
    return paciente
