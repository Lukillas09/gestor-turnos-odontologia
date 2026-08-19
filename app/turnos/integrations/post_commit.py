import logging
from functools import partial

from django.db import DatabaseError, transaction

logger = logging.getLogger(__name__)

GOOGLE_CREAR = "crear"
GOOGLE_ACTUALIZAR = "actualizar"
GOOGLE_CANCELAR = "cancelar"

EMAIL_CONFIRMADO = "confirmado"
EMAIL_CANCELADO = "cancelado"
EMAIL_REPROGRAMADO = "reprogramado"


def programar_integraciones_turno(turno_id, *, google=None, email=None):
    if google not in {None, GOOGLE_CREAR, GOOGLE_ACTUALIZAR, GOOGLE_CANCELAR}:
        raise ValueError("Operación de Google Calendar no soportada.")
    if email not in {None, EMAIL_CONFIRMADO, EMAIL_CANCELADO, EMAIL_REPROGRAMADO}:
        raise ValueError("Notificación de turno no soportada.")

    transaction.on_commit(
        partial(
            _ejecutar_integraciones_turno,
            turno_id,
            google=google,
            email=email,
        )
    )


def _ejecutar_integraciones_turno(turno_id, *, google=None, email=None):
    from turnos.models import Turno

    try:
        turno = (
            Turno.objects.select_related("paciente", "odontologo", "odontologo__usuario")
            .filter(pk=turno_id)
            .first()
        )
    except DatabaseError as error:
        _registrar_fallo("database", "load_turn", error)
        return

    if turno is None:
        logger.warning("No se ejecutaron integraciones post-commit. reason=turn_missing")
        return

    if google:
        _ejecutar_google(turno, google)

    if email:
        _ejecutar_email(turno, email)


def _ejecutar_google(turno, operacion):
    from turnos.google_calendar_sync import (
        sincronizar_turno_actualizado,
        sincronizar_turno_cancelado,
        sincronizar_turno_creado,
    )

    operaciones = {
        GOOGLE_CREAR: sincronizar_turno_creado,
        GOOGLE_ACTUALIZAR: sincronizar_turno_actualizado,
        GOOGLE_CANCELAR: sincronizar_turno_cancelado,
    }

    try:
        operaciones[operacion](turno)
    except Exception as error:
        _registrar_fallo("google_calendar", operacion, error)


def _ejecutar_email(turno, operacion):
    from turnos.notifications import (
        notificar_turno_cancelado,
        notificar_turno_confirmado,
        notificar_turno_reprogramado,
    )

    operaciones = {
        EMAIL_CONFIRMADO: notificar_turno_confirmado,
        EMAIL_CANCELADO: notificar_turno_cancelado,
        EMAIL_REPROGRAMADO: notificar_turno_reprogramado,
    }

    try:
        resultado = operaciones[operacion](turno)
    except Exception as error:
        _registrar_fallo("email", operacion, error)
        return

    if not resultado.enviada:
        logger.warning(
            "Integración post-commit no completada. provider=email operation=%s",
            operacion,
        )


def _registrar_fallo(proveedor, operacion, error):
    logger.warning(
        "Integración post-commit fallida. provider=%s operation=%s error_type=%s",
        proveedor,
        operacion,
        error.__class__.__name__,
    )
