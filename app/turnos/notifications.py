from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail


@dataclass(frozen=True)
class ResultadoNotificacionEmail:
    enviada: bool
    motivo: str = ""


def notificar_solicitud_turno_recibida(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Recibimos tu solicitud de turno",
        encabezado="Recibimos tu solicitud de turno.",
        mensaje_estado="El turno quedo pendiente de confirmacion por el consultorio.",
    )


def notificar_turno_confirmado(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue confirmado",
        encabezado="Tu turno fue confirmado.",
        mensaje_estado="Te esperamos en el dia y horario indicado.",
    )


def notificar_turno_cancelado(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue cancelado",
        encabezado="Tu turno fue cancelado.",
        mensaje_estado="Si necesitas un nuevo turno, comunicate con el consultorio.",
    )


def _enviar_email_turno(turno, asunto, encabezado, mensaje_estado):
    destinatario = turno.paciente.email

    if not destinatario:
        return ResultadoNotificacionEmail(
            enviada=False,
            motivo="El paciente no tiene email cargado.",
        )

    enviados = send_mail(
        subject=asunto,
        message=_construir_mensaje_turno(turno, encabezado, mensaje_estado),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[destinatario],
        fail_silently=True,
    )

    return ResultadoNotificacionEmail(enviada=enviados > 0)


def _construir_mensaje_turno(turno, encabezado, mensaje_estado):
    lineas = [
        f"Hola {turno.paciente.nombre},",
        "",
        encabezado,
        "",
        f"Paciente: {turno.paciente.nombre_completo}",
        f"Odontologo: {turno.odontologo.nombre_completo}",
        f"Fecha: {turno.fecha:%d/%m/%Y}",
        f"Horario: {turno.hora_inicio:%H:%M} a {turno.hora_fin:%H:%M}",
        f"Estado: {turno.get_estado_display()}",
    ]

    if turno.motivo:
        lineas.append(f"Motivo: {turno.motivo}")

    lineas.extend(
        [
            "",
            mensaje_estado,
            "",
            "Gestor de Turnos",
        ]
    )
    return "\n".join(lineas)
