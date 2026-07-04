from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from consultorio.services import obtener_configuracion_consultorio

from .models import ExcepcionAgenda, Odontologo, Turno, bloquear_agendas_de_turnos


ESTADOS_TURNO_ACTIVOS = [Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO]


@dataclass(frozen=True)
class RangoReservaPublica:
    fecha_minima: object
    fecha_maxima: object
    fecha_hora_minima: object
    ventana_dias: int
    permite_mismo_dia: bool
    anticipacion_minima_minutos: int


class TurnosAfectadosPorExcepcionError(Exception):
    def __init__(self, turnos, mensaje=None):
        self.turnos = list(turnos)
        self.mensaje = mensaje or (
            "La excepción afecta turnos pendientes o confirmados existentes."
        )
        super().__init__(self.mensaje)


def obtener_rango_reserva_publica(ahora=None):
    configuracion = obtener_configuracion_consultorio()
    momento = timezone.localtime(ahora or timezone.now())
    hoy = momento.date()
    ventana_dias = max(1, min(configuracion.ventana_reserva_publica_dias, 90))
    anticipacion = max(
        0,
        min(configuracion.anticipacion_minima_reserva_publica_minutos, 10080),
    )
    fecha_hora_minima = momento + timedelta(minutes=anticipacion)
    fecha_minima = fecha_hora_minima.date()

    if not configuracion.permitir_reserva_publica_mismo_dia and fecha_minima <= hoy:
        fecha_minima = hoy + timedelta(days=1)

    return RangoReservaPublica(
        fecha_minima=fecha_minima,
        fecha_maxima=hoy + timedelta(days=ventana_dias - 1),
        fecha_hora_minima=fecha_hora_minima,
        ventana_dias=ventana_dias,
        permite_mismo_dia=configuracion.permitir_reserva_publica_mismo_dia,
        anticipacion_minima_minutos=anticipacion,
    )


def fecha_en_rango_reserva_publica(fecha, ahora=None):
    rango = obtener_rango_reserva_publica(ahora)
    return rango.fecha_minima <= fecha <= rango.fecha_maxima


def validar_fecha_reserva_publica(fecha, ahora=None):
    momento = timezone.localtime(ahora or timezone.now())

    if fecha < momento.date():
        raise ValidationError("La fecha no puede ser anterior a hoy.")

    if not fecha_en_rango_reserva_publica(fecha, ahora):
        rango = obtener_rango_reserva_publica(ahora)
        raise ValidationError(
            (
                "La fecha debe estar dentro de la ventana pública de reserva "
                f"({rango.fecha_minima:%d/%m/%Y} a {rango.fecha_maxima:%d/%m/%Y})."
            )
        )


def validar_intervalo_reserva_publica(fecha, hora_inicio, duracion_minutos, ahora=None):
    validar_fecha_reserva_publica(fecha, ahora)
    rango = obtener_rango_reserva_publica(ahora)
    inicio = timezone.make_aware(
        datetime.combine(fecha, hora_inicio),
        timezone.get_current_timezone(),
    )

    if inicio < rango.fecha_hora_minima:
        raise ValidationError(
            "El horario elegido requiere más anticipación. Elegí otro horario disponible."
        )


def obtener_horarios_publicos_disponibles(
    odontologo,
    fecha,
    duracion_minutos,
    intervalo_minutos=None,
    turno_excluido=None,
    ahora=None,
):
    if not fecha_en_rango_reserva_publica(fecha, ahora):
        return []

    from .selectors import obtener_horarios_disponibles

    horarios = obtener_horarios_disponibles(
        odontologo=odontologo,
        fecha=fecha,
        duracion_minutos=duracion_minutos,
        intervalo_minutos=intervalo_minutos,
        turno_excluido=turno_excluido,
    )
    horarios_validos = []

    for horario in horarios:
        try:
            validar_intervalo_reserva_publica(
                fecha,
                horario,
                duracion_minutos,
                ahora=ahora,
            )
        except ValidationError:
            continue
        horarios_validos.append(horario)

    return horarios_validos


def obtener_excepciones_activas(odontologo, fecha_desde, fecha_hasta):
    excepciones = ExcepcionAgenda.objects.select_related("odontologo").filter(
        activo=True,
        fecha_desde__lte=fecha_hasta,
        fecha_hasta__gte=fecha_desde,
    )

    if odontologo:
        excepciones = excepciones.filter(Q(odontologo=odontologo) | Q(odontologo__isnull=True))

    return excepciones.order_by("fecha_desde", "hora_inicio", "odontologo_id")


def obtener_excepciones_para_fecha(fecha, odontologo=None):
    return obtener_excepciones_activas(odontologo, fecha, fecha)


def obtener_excepcion_que_bloquea_intervalo(
    odontologo,
    fecha,
    hora_inicio,
    hora_fin,
    excepciones=None,
):
    excepciones = excepciones or obtener_excepciones_para_fecha(fecha, odontologo)

    for excepcion in excepciones:
        if excepcion.bloquea_intervalo(fecha, hora_inicio, hora_fin):
            return excepcion

    return None


def validar_intervalo_sin_excepcion(odontologo, fecha, hora_inicio, hora_fin):
    excepcion = obtener_excepcion_que_bloquea_intervalo(
        odontologo,
        fecha,
        hora_inicio,
        hora_fin,
    )

    if excepcion:
        raise ValidationError(
            {
                "hora_inicio": (
                    "El horario está bloqueado por una excepción de agenda: "
                    f"{excepcion.get_tipo_display()} ({excepcion.alcance_display})."
                )
            }
        )


def obtener_turnos_afectados_por_excepcion(excepcion):
    if not excepcion.activo:
        return []

    turnos = Turno.objects.select_related(
        "paciente",
        "odontologo",
        "odontologo__usuario",
    ).filter(
        fecha__gte=excepcion.fecha_desde,
        fecha__lte=excepcion.fecha_hasta,
        estado__in=ESTADOS_TURNO_ACTIVOS,
    )

    if excepcion.odontologo_id:
        turnos = turnos.filter(odontologo_id=excepcion.odontologo_id)

    return [
        turno
        for turno in turnos.order_by("fecha", "hora_inicio")
        if excepcion.bloquea_intervalo(turno.fecha, turno.hora_inicio, turno.hora_fin)
    ]


def crear_excepcion_agenda(datos, usuario=None, confirmar_afectados=False):
    with transaction.atomic():
        excepcion = ExcepcionAgenda(**datos)
        excepcion.creada_por = usuario if getattr(usuario, "is_authenticated", False) else None
        excepcion.actualizada_por = excepcion.creada_por
        excepcion.full_clean()
        _bloquear_agendas_para_excepcion(excepcion)
        afectados = obtener_turnos_afectados_por_excepcion(excepcion)

        if afectados and not confirmar_afectados:
            raise TurnosAfectadosPorExcepcionError(afectados)

        excepcion.save()
        return excepcion


def actualizar_excepcion_agenda(excepcion, datos, usuario=None, confirmar_afectados=False):
    with transaction.atomic():
        original = ExcepcionAgenda.objects.select_for_update().get(pk=excepcion.pk)
        _bloquear_agendas_para_excepcion(original)

        for campo, valor in datos.items():
            setattr(original, campo, valor)

        original.actualizada_por = usuario if getattr(usuario, "is_authenticated", False) else None
        original.full_clean()
        _bloquear_agendas_para_excepcion(original)
        afectados = obtener_turnos_afectados_por_excepcion(original)

        if afectados and not confirmar_afectados:
            raise TurnosAfectadosPorExcepcionError(afectados)

        original.save()
        return original


def desactivar_excepcion_agenda(excepcion, usuario=None):
    with transaction.atomic():
        excepcion = ExcepcionAgenda.objects.select_for_update().get(pk=excepcion.pk)
        _bloquear_agendas_para_excepcion(excepcion)
        excepcion.activo = False
        excepcion.desactivada_en = timezone.now()
        excepcion.desactivada_por = (
            usuario if getattr(usuario, "is_authenticated", False) else None
        )
        excepcion.actualizada_por = excepcion.desactivada_por
        excepcion.save(
            update_fields=[
                "activo",
                "desactivada_en",
                "desactivada_por",
                "actualizada_por",
                "actualizado_en",
            ]
        )
        return excepcion


def _bloquear_agendas_para_excepcion(excepcion):
    claves = _obtener_claves_bloqueo_para_excepcion(excepcion)
    return bloquear_agendas_de_turnos(claves)


def _obtener_claves_bloqueo_para_excepcion(excepcion):
    odontologo_ids = _obtener_odontologos_a_bloquear(excepcion)
    fechas = [
        excepcion.fecha_desde + timedelta(days=offset)
        for offset in range((excepcion.fecha_hasta - excepcion.fecha_desde).days + 1)
    ]
    return [(odontologo_id, fecha) for odontologo_id in odontologo_ids for fecha in fechas]


def _obtener_odontologos_a_bloquear(excepcion):
    if excepcion.odontologo_id:
        return [excepcion.odontologo_id]

    odontologos_activos = Odontologo.objects.filter(activo=True).values_list("pk", flat=True)
    odontologos_con_turnos = Turno.objects.filter(
        fecha__gte=excepcion.fecha_desde,
        fecha__lte=excepcion.fecha_hasta,
        estado__in=ESTADOS_TURNO_ACTIVOS,
    ).values_list("odontologo_id", flat=True)
    return sorted(set(odontologos_activos) | set(odontologos_con_turnos))
