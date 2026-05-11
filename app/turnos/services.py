from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from pacientes.models import Paciente

from .google_calendar_sync import (
    sincronizar_turno_actualizado,
    sincronizar_turno_cancelado,
    sincronizar_turno_creado,
)
from .models import Turno
from .notifications import (
    notificar_recordatorio_turno,
    notificar_solicitud_turno_recibida,
    notificar_turno_cancelado,
    notificar_turno_confirmado,
    notificar_turno_reprogramado,
)


@dataclass(frozen=True)
class ResultadoEnvioRecordatoriosEmail:
    encontrados: int
    enviados: int
    fallidos: int


def crear_turno_desde_formulario(form):
    turno = form.save()
    sincronizar_turno_creado(turno)
    return turno


def actualizar_turno_desde_formulario(form):
    turno = form.save()
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
    if turno.estado != Turno.Estado.PENDIENTE:
        return turno

    turno.estado = Turno.Estado.CONFIRMADO
    turno.save(update_fields=["estado", "actualizado_en"])
    sincronizar_turno_actualizado(turno)
    notificar_turno_confirmado(turno)
    return turno


def cancelar_turno(turno):
    if turno.estado == Turno.Estado.CANCELADO:
        return turno

    turno.estado = Turno.Estado.CANCELADO
    turno.save(update_fields=["estado", "actualizado_en"])
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
        duracion_minutos=odontologo.duracion_turno_minutos,
        motivo=datos["motivo"],
        estado=Turno.Estado.PENDIENTE,
    )
    sincronizar_turno_creado(turno)
    notificar_solicitud_turno_recibida(turno)
    return turno


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
