import logging
from dataclasses import dataclass

from .integrations.google_calendar import (
    GoogleCalendarError,
    crear_cliente_desde_conexion,
)
from .models import GoogleCalendarConexion, Turno

ACCION_CREAR = "crear"
ACCION_ACTUALIZAR = "actualizar"
ACCION_CANCELAR = "cancelar"
MENSAJE_ERROR_INESPERADO = "Error inesperado al sincronizar con Google Calendar."

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoSincronizacionGoogleCalendar:
    realizada: bool
    accion: str
    mensaje: str = ""
    event_id: str = ""


def sincronizar_turno_creado(turno, cliente_factory=crear_cliente_desde_conexion):
    if turno.estado == Turno.Estado.CANCELADO:
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=ACCION_CREAR,
            mensaje="No se crea evento para un turno cancelado.",
        )

    if turno.google_calendar_event_id:
        return sincronizar_turno_actualizado(turno, cliente_factory)

    return _sincronizar(
        turno=turno,
        accion=ACCION_CREAR,
        operacion=_crear_evento,
        cliente_factory=cliente_factory,
    )


def sincronizar_turno_actualizado(turno, cliente_factory=crear_cliente_desde_conexion):
    if turno.estado == Turno.Estado.CANCELADO:
        return sincronizar_turno_cancelado(turno, cliente_factory)

    if not turno.google_calendar_event_id:
        return sincronizar_turno_creado(turno, cliente_factory)

    return _sincronizar(
        turno=turno,
        accion=ACCION_ACTUALIZAR,
        operacion=_actualizar_evento,
        cliente_factory=cliente_factory,
    )


def sincronizar_turno_cancelado(turno, cliente_factory=crear_cliente_desde_conexion):
    if not turno.google_calendar_event_id:
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=ACCION_CANCELAR,
            mensaje="El turno no tiene evento de Google Calendar para cancelar.",
        )

    return _sincronizar(
        turno=turno,
        accion=ACCION_CANCELAR,
        operacion=_cancelar_evento,
        cliente_factory=cliente_factory,
    )


def _sincronizar(turno, accion, operacion, cliente_factory):
    conexion = _obtener_conexion_activa(turno)

    if not conexion:
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=accion,
            mensaje="El odontólogo no tiene una conexión activa con Google Calendar.",
        )

    if not conexion.esta_conectada:
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=accion,
            mensaje="La conexión de Google Calendar no tiene refresh token.",
        )

    try:
        cliente = cliente_factory(conexion)
        event_id = operacion(cliente, turno)
    except GoogleCalendarError as error:
        conexion.registrar_error(str(error))
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=accion,
            mensaje=str(error),
        )
    except Exception:
        logger.exception(
            "Error inesperado al sincronizar el turno %s con Google Calendar.",
            turno.pk,
        )
        conexion.registrar_error(MENSAJE_ERROR_INESPERADO)
        return ResultadoSincronizacionGoogleCalendar(
            realizada=False,
            accion=accion,
            mensaje=MENSAJE_ERROR_INESPERADO,
        )

    conexion.marcar_sincronizada()
    return ResultadoSincronizacionGoogleCalendar(
        realizada=True,
        accion=accion,
        event_id=event_id or "",
    )


def _obtener_conexion_activa(turno):
    return GoogleCalendarConexion.objects.filter(
        odontologo=turno.odontologo,
        activa=True,
    ).first()


def _crear_evento(cliente, turno):
    event_id = cliente.crear_evento(turno)

    if event_id:
        turno.google_calendar_event_id = event_id
        turno.save(update_fields=["google_calendar_event_id", "actualizado_en"])

    return event_id


def _actualizar_evento(cliente, turno):
    event_id = cliente.actualizar_evento(turno)

    if event_id and event_id != turno.google_calendar_event_id:
        turno.google_calendar_event_id = event_id
        turno.save(update_fields=["google_calendar_event_id", "actualizado_en"])

    return event_id


def _cancelar_evento(cliente, turno):
    cliente.cancelar_evento(turno)
    turno.google_calendar_event_id = ""
    turno.save(update_fields=["google_calendar_event_id", "actualizado_en"])
    return ""
