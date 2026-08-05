import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from consultorio.services import obtener_configuracion_consultorio

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


def notificar_solicitud_turno_contacto_existente(solicitud, fail_silently=True):
    return _enviar_email_turno(
        turno=solicitud.turno,
        asunto="Solicitud de turno recibida",
        template_name="turnos/emails/solicitud_contacto_existente.txt",
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


def notificar_codigo_acceso_publico_turnos(paciente, codigo, expira_en, fail_silently=True):
    if not paciente.email:
        return ResultadoNotificacionEmail(
            enviada=False,
            motivo="El paciente no tiene email cargado.",
        )

    configuracion = obtener_configuracion_consultorio()

    try:
        enviados = send_mail(
            subject="Código de acceso a tus turnos",
            message=render_to_string(
                "turnos/emails/codigo_acceso_publico.txt",
                {
                    "codigo": codigo,
                    "expira_en": expira_en,
                    "configuracion_consultorio": configuracion,
                    "consultorio": configuracion.nombre_visible,
                },
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[paciente.email],
            fail_silently=False,
        )
    except Exception as error:
        logger.warning(
            "No se pudo enviar una notificación. operation=public_access_code " "error_type=%s",
            error.__class__.__name__,
        )

        if not fail_silently:
            raise

        return ResultadoNotificacionEmail(enviada=False, motivo=error.__class__.__name__)

    return ResultadoNotificacionEmail(enviada=enviados > 0)


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
        logger.warning(
            "No se pudo enviar una notificación. operation=appointment_email " "error_type=%s",
            error.__class__.__name__,
        )

        if not fail_silently:
            raise

        return ResultadoNotificacionEmail(
            enviada=False,
            motivo=error.__class__.__name__,
        )

    return ResultadoNotificacionEmail(enviada=enviados > 0)


def _renderizar_email_turno(turno, template_name):
    configuracion = obtener_configuracion_consultorio()

    return render_to_string(
        template_name,
        {
            "turno": turno,
            "paciente": turno.paciente,
            "odontologo": turno.odontologo,
            "configuracion_consultorio": configuracion,
            "consultorio": configuracion.nombre_visible,
        },
    )
