from datetime import datetime, timedelta

from .models import DisponibilidadOdontologo, Turno


def obtener_horarios_disponibles(odontologo, fecha, duracion_minutos=None, intervalo_minutos=None):
    if not odontologo.activo:
        return []

    duracion = duracion_minutos or odontologo.duracion_turno_minutos
    intervalo = intervalo_minutos or duracion

    if duracion <= 0 or intervalo <= 0:
        return []

    disponibilidades = DisponibilidadOdontologo.objects.filter(
        odontologo=odontologo,
        dia_semana=fecha.weekday(),
        activo=True,
    ).order_by("hora_inicio")
    turnos_ocupados = Turno.objects.filter(
        odontologo=odontologo,
        fecha=fecha,
        estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
    )

    horarios = []

    for disponibilidad in disponibilidades:
        horarios.extend(
            _obtener_horarios_del_bloque(
                fecha=fecha,
                hora_inicio=disponibilidad.hora_inicio,
                hora_fin=disponibilidad.hora_fin,
                duracion_minutos=duracion,
                intervalo_minutos=intervalo,
                turnos_ocupados=turnos_ocupados,
            )
        )

    return horarios


def _obtener_horarios_del_bloque(
    fecha,
    hora_inicio,
    hora_fin,
    duracion_minutos,
    intervalo_minutos,
    turnos_ocupados,
):
    horarios = []
    inicio_bloque = datetime.combine(fecha, hora_inicio)
    fin_bloque = datetime.combine(fecha, hora_fin)
    inicio = inicio_bloque

    while inicio + timedelta(minutes=duracion_minutos) <= fin_bloque:
        fin = inicio + timedelta(minutes=duracion_minutos)

        if not _se_solapa_con_turnos(inicio, fin, turnos_ocupados):
            horarios.append(inicio.time())

        inicio += timedelta(minutes=intervalo_minutos)

    return horarios


def _se_solapa_con_turnos(inicio, fin, turnos):
    for turno in turnos:
        if inicio < turno.fecha_hora_fin and fin > turno.fecha_hora_inicio:
            return True

    return False
