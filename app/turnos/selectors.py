from datetime import datetime, timedelta

from .models import DisponibilidadOdontologo, Turno


def obtener_turnos_del_dia(fecha, odontologo=None):
    turnos = _turnos_con_relaciones().filter(fecha=fecha)

    if odontologo:
        turnos = turnos.filter(odontologo=odontologo)

    return turnos


def obtener_bloques_agenda_del_dia(fecha, odontologo=None, intervalo_minutos=30):
    turnos = list(obtener_turnos_del_dia(fecha, odontologo))
    rango_agenda = _obtener_rango_agenda(fecha, odontologo, turnos)

    if not rango_agenda:
        return []

    hora_inicio, hora_fin = rango_agenda
    inicio = datetime.combine(fecha, hora_inicio)
    fin = datetime.combine(fecha, hora_fin)
    intervalo = timedelta(minutes=intervalo_minutos)
    bloques = []

    while inicio < fin:
        fin_bloque = inicio + intervalo
        bloques.append(
            {
                "hora_inicio": inicio.time(),
                "hora_fin": fin_bloque.time(),
                "turnos": [
                    turno
                    for turno in turnos
                    if inicio <= turno.fecha_hora_inicio < fin_bloque
                ],
            }
        )
        inicio = fin_bloque

    return bloques


def obtener_turnos_de_la_semana(fecha_referencia, odontologo=None):
    inicio_semana = obtener_inicio_semana(fecha_referencia)
    fin_semana = inicio_semana + timedelta(days=6)
    turnos = _turnos_con_relaciones().filter(
        fecha__gte=inicio_semana,
        fecha__lte=fin_semana,
    )

    if odontologo:
        turnos = turnos.filter(odontologo=odontologo)

    turnos_por_fecha = {}

    for turno in turnos:
        turnos_por_fecha.setdefault(turno.fecha, []).append(turno)

    return [
        {
            "fecha": inicio_semana + timedelta(days=dia),
            "turnos": turnos_por_fecha.get(inicio_semana + timedelta(days=dia), []),
        }
        for dia in range(7)
    ]


def obtener_inicio_semana(fecha):
    return fecha - timedelta(days=fecha.weekday())


def _turnos_con_relaciones():
    return Turno.objects.select_related(
        "paciente",
        "odontologo",
        "odontologo__usuario",
    )


def _obtener_rango_agenda(fecha, odontologo, turnos):
    disponibilidades = DisponibilidadOdontologo.objects.filter(
        dia_semana=fecha.weekday(),
        activo=True,
        odontologo__activo=True,
    )

    if odontologo:
        disponibilidades = disponibilidades.filter(odontologo=odontologo)

    horas_inicio = [disponibilidad.hora_inicio for disponibilidad in disponibilidades]
    horas_fin = [disponibilidad.hora_fin for disponibilidad in disponibilidades]

    horas_inicio.extend(turno.hora_inicio for turno in turnos)
    horas_fin.extend(turno.hora_fin for turno in turnos)

    if not horas_inicio or not horas_fin:
        return None

    return min(horas_inicio), max(horas_fin)


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
