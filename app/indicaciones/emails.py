import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from historias.access_policy import obtener_politica_escritura
from historias.models import AccesoClinicoAuditoria

from .audit import registrar_evento_indicacion
from .models import IndicacionPaciente
from .permissions import indicaciones_habilitadas, puede_reenviar_indicacion

logger = logging.getLogger(__name__)

ERROR_EMAIL_NEUTRAL = "No se pudo entregar el email al proveedor configurado."


class EntregaIndicacionError(RuntimeError):
    pass


def enviar_indicacion_por_email(
    *,
    indicacion_id,
    usuario=None,
    request=None,
    automatico=False,
    forzar=False,
    usar_email_actual=False,
):
    if not indicaciones_habilitadas():
        raise PermissionDenied("El módulo de indicaciones postoperatorias está deshabilitado.")

    with transaction.atomic():
        indicacion = (
            IndicacionPaciente.objects.select_for_update()
            .select_related(
                "paciente",
                "odontologo",
                "odontologo__usuario",
                "emitida_por",
            )
            .get(pk=indicacion_id)
        )
        if indicacion.estado != IndicacionPaciente.Estado.EMITIDA:
            raise ValidationError("Solo se pueden enviar indicaciones emitidas y vigentes.")
        if usuario is not None and not puede_reenviar_indicacion(usuario, indicacion):
            raise PermissionDenied("No tenés permiso para reenviar esta indicación.")
        if indicacion.email_estado == IndicacionPaciente.EstadoEmail.ENVIADO and not forzar:
            return True

        destino = indicacion.email_destino
        if usar_email_actual:
            destino = _email_actual_verificado(indicacion)
            if not destino:
                raise ValidationError("El paciente no tiene un email actual verificado.")
            indicacion.email_destino = destino
            indicacion.email_clave_idempotencia = ""
        if not destino:
            indicacion.email_estado = IndicacionPaciente.EstadoEmail.SIN_DESTINO
            indicacion.ultimo_error_email = ""
            indicacion.save(permitir_actualizacion_email=True)
            return False

        if forzar and indicacion.email_estado == IndicacionPaciente.EstadoEmail.ENVIADO:
            indicacion.email_clave_idempotencia = ""
        if not indicacion.email_clave_idempotencia:
            indicacion.email_clave_idempotencia = (
                f"indicacion-{indicacion.uuid}-envio-{indicacion.email_intentos + 1}"
            )

        indicacion.email_intentos += 1
        indicacion.email_ultimo_intento_en = timezone.now()
        indicacion.email_estado = IndicacionPaciente.EstadoEmail.ENVIANDO
        indicacion.ultimo_error_email = ""
        indicacion.save(permitir_actualizacion_email=True)

        try:
            pdf_bytes = _leer_pdf(indicacion)
            mensaje = EmailMessage(
                subject="Indicaciones de tu atención odontológica",
                body=_cuerpo_email(indicacion),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[destino],
                headers={"Idempotency-Key": indicacion.email_clave_idempotencia},
            )
            mensaje.attach(
                _nombre_pdf(indicacion),
                pdf_bytes,
                "application/pdf",
            )
            enviados = mensaje.send(fail_silently=False)
            if enviados != 1:
                raise EntregaIndicacionError("El backend no confirmó el envío.")
        except Exception as error:
            indicacion.email_estado = IndicacionPaciente.EstadoEmail.ERROR
            indicacion.ultimo_error_email = ERROR_EMAIL_NEUTRAL
            indicacion.save(permitir_actualizacion_email=True)
            logger.warning(
                "Falló el email de una indicación emitida. indicacion_id=%s error_type=%s",
                indicacion.pk,
                error.__class__.__name__,
            )
            registrar_evento_indicacion(
                request=request,
                usuario=usuario or indicacion.emitida_por,
                accion=AccesoClinicoAuditoria.Accion.ERROR_EMAIL_INDICACION,
                indicacion=indicacion,
                resultado=AccesoClinicoAuditoria.Resultado.ERROR,
                politica=_politica_email(indicacion, usuario, automatico),
                motivo="El envío de email no pudo completarse.",
            )
            return False

        indicacion.email_estado = IndicacionPaciente.EstadoEmail.ENVIADO
        indicacion.email_enviado_en = timezone.now()
        indicacion.ultimo_error_email = ""
        indicacion.save(permitir_actualizacion_email=True)
        registrar_evento_indicacion(
            request=request,
            usuario=usuario or indicacion.emitida_por,
            accion=(
                AccesoClinicoAuditoria.Accion.ENVIAR_EMAIL_INDICACION
                if automatico and indicacion.email_intentos == 1
                else AccesoClinicoAuditoria.Accion.REENVIAR_EMAIL_INDICACION
            ),
            indicacion=indicacion,
            politica=_politica_email(indicacion, usuario, automatico),
            motivo=(
                "Email de indicación enviado."
                if automatico and indicacion.email_intentos == 1
                else "Reenvío de indicación completado."
            ),
        )
        return True


def reenviar_indicacion(
    *,
    indicacion,
    usuario,
    request=None,
    usar_email_actual=False,
):
    return enviar_indicacion_por_email(
        indicacion_id=indicacion.pk,
        usuario=usuario,
        request=request,
        automatico=False,
        forzar=True,
        usar_email_actual=usar_email_actual,
    )


def _leer_pdf(indicacion):
    if not indicacion.pdf:
        raise EntregaIndicacionError("La indicación no tiene PDF.")
    with indicacion.pdf.open("rb") as archivo:
        contenido = archivo.read(settings.INDICACIONES_PDF_MAX_BYTES + 1)
    if not contenido.startswith(b"%PDF-"):
        raise EntregaIndicacionError("El archivo almacenado no es un PDF válido.")
    if len(contenido) > settings.INDICACIONES_PDF_MAX_BYTES:
        raise EntregaIndicacionError("El PDF supera el tamaño permitido.")
    return contenido


def _cuerpo_email(indicacion):
    nombre = indicacion.snapshot_paciente.get("nombre", "").strip()
    saludo = f"Hola, {nombre}:" if nombre else "Hola:"
    return (
        f"{saludo}\n\n"
        "Adjuntamos las indicaciones correspondientes a tu atención.\n\n"
        "Conservá este documento y seguí las indicaciones brindadas por tu profesional.\n"
        "Ante cualquier duda, comunicate con el consultorio.\n\n"
        "Este documento no constituye una receta electrónica de medicamentos."
    )


def _nombre_pdf(indicacion):
    fecha = timezone.localtime(indicacion.emitida_en).date()
    return f"indicaciones-{fecha:%Y-%m-%d}.pdf"


def _email_actual_verificado(indicacion):
    paciente = indicacion.paciente
    if paciente.activo and paciente.email and paciente.email_verificado_en:
        return paciente.email.strip()
    return ""


def _politica_email(indicacion, usuario, automatico):
    if automatico or usuario is None:
        return AccesoClinicoAuditoria.Politica.SISTEMA
    return (
        obtener_politica_escritura(usuario, indicacion.paciente)
        or AccesoClinicoAuditoria.Politica.SIN_PERMISO
    )
