from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string


@dataclass(frozen=True)
class ResultadoNotificacionEmail:
    enviada: bool
    motivo: str = ""


def notificar_solicitud_turno_recibida(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Recibimos tu solicitud de turno",
        template_name="turnos/emails/solicitud_recibida.txt",
    )


def notificar_turno_confirmado(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue confirmado",
        template_name="turnos/emails/turno_confirmado.txt",
    )


def notificar_turno_cancelado(turno):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue cancelado",
        template_name="turnos/emails/turno_cancelado.txt",
    )


def _enviar_email_turno(turno, asunto, template_name):
    destinatario = turno.paciente.email

    if not destinatario:
        return ResultadoNotificacionEmail(
            enviada=False,
            motivo="El paciente no tiene email cargado.",
        )

    enviados = send_mail(
        subject=asunto,
        message=_renderizar_email_turno(turno, template_name),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[destinatario],
        fail_silently=True,
    )

    return ResultadoNotificacionEmail(enviada=enviados > 0)


def _renderizar_email_turno(turno, template_name):
    return render_to_string(
        template_name,
        {
            "turno": turno,
            "paciente": turno.paciente,
            "odontologo": turno.odontologo,
        },
    )
