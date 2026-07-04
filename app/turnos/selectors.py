from datetime import datetime, timedelta

from django.db.models import Q

from .models import DisponibilidadOdontologo, Odontologo, Turno
from .excepciones import obtener_excepciones_activas, obtener_excepcion_que_bloquea_intervalo


def obtener_turnos_del_dia(fecha, odontologo=None, busqueda=""):
    turnos = _turnos_con_relaciones().filter(fecha=fecha)

    if odontologo:
        turnos = turnos.filter(odontologo=odontologo)

    turnos = _filtrar_turnos_por_busqueda(turnos, busqueda)

    return turnos


def obtener_bloques_agenda_del_dia(
    fecha,
    odontologo=None,
    intervalo_minutos=30,
    busqueda="",
):
    turnos = list(obtener_turnos_del_dia(fecha, odontologo, busqueda))
    return _construir_bloques_agenda_del_dia(
        fecha,
        odontologo,
        turnos,
        intervalo_minutos,
    )


def _construir_bloques_agenda_del_dia(
    fecha,
    odontologo,
    turnos,
    intervalo_minutos=30,
):
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


def obtener_agenda_diaria_por_odontologo(fecha, odontologo=None, busqueda=""):
    odontologos = _obtener_odontologos_para_agenda(fecha, fecha, odontologo, busqueda)

    agendas = []

    for odontologo_agenda in odontologos:
        turnos = list(obtener_turnos_del_dia(fecha, odontologo_agenda, busqueda))
        agendas.append(
            {
                "odontologo": odontologo_agenda,
                "bloques": _construir_bloques_agenda_del_dia(
                    fecha,
                    odontologo_agenda,
                    turnos,
                ),
                "turnos": turnos,
            }
        )

    return agendas


def obtener_turnos_de_la_semana(fecha_referencia, odontologo=None, busqueda=""):
    inicio_semana = obtener_inicio_semana(fecha_referencia)
    fin_semana = inicio_semana + timedelta(days=6)
    turnos = _turnos_con_relaciones().filter(
        fecha__gte=inicio_semana,
        fecha__lte=fin_semana,
    )

    if odontologo:
        turnos = turnos.filter(odontologo=odontologo)

    turnos = _filtrar_turnos_por_busqueda(turnos, busqueda)

    return _construir_dias_semana(inicio_semana, turnos)


def _construir_dias_semana(inicio_semana, turnos):
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


def obtener_agenda_semanal_por_odontologo(fecha_referencia, odontologo=None, busqueda=""):
    inicio_semana = obtener_inicio_semana(fecha_referencia)
    fin_semana = inicio_semana + timedelta(days=6)
    odontologos = _obtener_odontologos_para_agenda(
        inicio_semana,
        fin_semana,
        odontologo,
        busqueda,
    )

    agendas = []

    for odontologo_agenda in odontologos:
        turnos = list(
            obtener_turnos_de_la_semana_como_queryset(
                inicio_semana,
                fin_semana,
                odontologo_agenda,
                busqueda,
            )
        )
        agendas.append(
            {
                "odontologo": odontologo_agenda,
                "dias": _construir_dias_semana(inicio_semana, turnos),
                "turnos": turnos,
            }
        )

    return agendas


def obtener_turnos_de_la_semana_como_queryset(
    inicio_semana,
    fin_semana,
    odontologo=None,
    busqueda="",
):
    turnos = _turnos_con_relaciones().filter(
        fecha__gte=inicio_semana,
        fecha__lte=fin_semana,
    )

    if odontologo:
        turnos = turnos.filter(odontologo=odontologo)

    turnos = _filtrar_turnos_por_busqueda(turnos, busqueda)

    return turnos


def obtener_resumen_estados(turnos):
    resumen = {
        "total": 0,
        Turno.Estado.PENDIENTE: 0,
        Turno.Estado.CONFIRMADO: 0,
        Turno.Estado.CANCELADO: 0,
    }

    for turno in turnos:
        resumen["total"] += 1
        resumen[turno.estado] = resumen.get(turno.estado, 0) + 1

    return resumen


def obtener_turno_superpuesto(
    odontologo,
    fecha,
    hora_inicio,
    duracion_minutos,
    turno_excluido=None,
):
    inicio_nuevo = datetime.combine(fecha, hora_inicio)
    fin_nuevo = inicio_nuevo + timedelta(minutes=duracion_minutos)
    turnos_activos = _turnos_con_relaciones().filter(
        odontologo=odontologo,
        fecha=fecha,
        estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
    )

    if turno_excluido and turno_excluido.pk:
        turnos_activos = turnos_activos.exclude(pk=turno_excluido.pk)

    for turno in turnos_activos.order_by("hora_inicio"):
        if inicio_nuevo < turno.fecha_hora_fin and fin_nuevo > turno.fecha_hora_inicio:
            return turno

    return None


def obtener_inicio_semana(fecha):
    return fecha - timedelta(days=fecha.weekday())


def _turnos_con_relaciones():
    return Turno.objects.select_related(
        "paciente",
        "odontologo",
        "odontologo__usuario",
        "solicitud_publica",
        "solicitud_publica__revisada_por",
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


def _obtener_odontologos_para_agenda(fecha_desde, fecha_hasta, odontologo=None, busqueda=""):
    if odontologo:
        return [odontologo]

    dias_semana = {
        (fecha_desde + timedelta(days=offset)).weekday()
        for offset in range((fecha_hasta - fecha_desde).days + 1)
    }
    ids_con_turnos = Turno.objects.filter(
        fecha__gte=fecha_desde,
        fecha__lte=fecha_hasta,
    )
    ids_con_turnos = _filtrar_turnos_por_busqueda(ids_con_turnos, busqueda).values_list(
        "odontologo_id",
        flat=True,
    )

    if busqueda:
        odontologo_ids = set(ids_con_turnos)
    else:
        ids_con_disponibilidad = DisponibilidadOdontologo.objects.filter(
            dia_semana__in=dias_semana,
            activo=True,
            odontologo__activo=True,
        ).values_list("odontologo_id", flat=True)
        odontologo_ids = set(ids_con_disponibilidad) | set(ids_con_turnos)

    if not odontologo_ids:
        return []

    return list(
        Odontologo.objects.filter(pk__in=odontologo_ids)
        .select_related("usuario")
        .order_by("usuario__last_name", "usuario__first_name", "usuario__username")
    )


def _filtrar_turnos_por_busqueda(turnos, busqueda):
    busqueda = (busqueda or "").strip()

    if not busqueda:
        return turnos

    return turnos.filter(
        Q(paciente__nombre__icontains=busqueda)
        | Q(paciente__apellido__icontains=busqueda)
        | Q(paciente__documento__icontains=busqueda)
        | Q(paciente__telefono__icontains=busqueda)
        | Q(paciente__email__icontains=busqueda)
        | Q(motivo__icontains=busqueda)
        | Q(odontologo__usuario__first_name__icontains=busqueda)
        | Q(odontologo__usuario__last_name__icontains=busqueda)
        | Q(odontologo__usuario__username__icontains=busqueda)
    )


def obtener_horarios_disponibles(
    odontologo,
    fecha,
    duracion_minutos=None,
    intervalo_minutos=None,
    turno_excluido=None,
):
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
    excepciones = list(obtener_excepciones_activas(odontologo, fecha, fecha))

    if turno_excluido and turno_excluido.pk:
        turnos_ocupados = turnos_ocupados.exclude(pk=turno_excluido.pk)

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
                excepciones=excepciones,
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
    excepciones,
):
    horarios = []
    inicio_bloque = datetime.combine(fecha, hora_inicio)
    fin_bloque = datetime.combine(fecha, hora_fin)
    inicio = inicio_bloque

    while inicio + timedelta(minutes=duracion_minutos) <= fin_bloque:
        fin = inicio + timedelta(minutes=duracion_minutos)

        if not _se_solapa_con_turnos(inicio, fin, turnos_ocupados) and not _se_solapa_con_excepciones(
            fecha,
            inicio.time(),
            fin.time(),
            excepciones,
        ):
            horarios.append(inicio.time())

        inicio += timedelta(minutes=intervalo_minutos)

    return horarios


def _se_solapa_con_turnos(inicio, fin, turnos):
    for turno in turnos:
        if inicio < turno.fecha_hora_fin and fin > turno.fecha_hora_inicio:
            return True

    return False


def _se_solapa_con_excepciones(fecha, hora_inicio, hora_fin, excepciones):
    return obtener_excepcion_que_bloquea_intervalo(
        odontologo=None,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        excepciones=excepciones,
    ) is not None
