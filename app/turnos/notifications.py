from dataclasses import dataclass
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoNotificacionEmail:
    enviada: bool
    motivo: str = ""


def notificar_solicitud_turno_recibida(turno, fail_silently=True):
    return _enviar_email_turno(
        turno=turno,
        asunto="Recibimos tu solicitud de turno",
        template_name="turnos/emails/solicitud_recibida.txt",
        fail_silently=fail_silently,
    )


def notificar_turno_confirmado(turno, fail_silently=True):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue confirmado",
        template_name="turnos/emails/turno_confirmado.txt",
        fail_silently=fail_silently,
    )


def notificar_turno_cancelado(turno, fail_silently=True):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue cancelado",
        template_name="turnos/emails/turno_cancelado.txt",
        fail_silently=fail_silently,
    )


def notificar_turno_reprogramado(turno, fail_silently=True):
    return _enviar_email_turno(
        turno=turno,
        asunto="Tu turno fue reprogramado",
        template_name="turnos/emails/turno_reprogramado.txt",
        fail_silently=fail_silently,
    )


def notificar_recordatorio_turno(turno, fail_silently=True):
    return _enviar_email_turno(
        turno=turno,
        asunto="Recordatorio de tu turno",
        template_name="turnos/emails/recordatorio_turno.txt",
        fail_silently=fail_silently,
    )


def _enviar_email_turno(turno, asunto, template_name, fail_silently=True):
    destinatario = turno.paciente.email

    if not destinatario:
        return ResultadoNotificacionEmail(
            enviada=False,
            motivo="El paciente no tiene email cargado.",
        )

    try:
        enviados = send_mail(
            subject=asunto,
            message=_renderizar_email_turno(turno, template_name),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[destinatario],
            fail_silently=False,
        )
    except Exception as error:
        logger.exception("No se pudo enviar el email '%s' a %s.", asunto, destinatario)

        if not fail_silently:
            raise

        return ResultadoNotificacionEmail(
            enviada=False,
            motivo=str(error),
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
