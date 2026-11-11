import logging
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from time import perf_counter

from django.utils import timezone

from .excepciones import (
    RangoReservaPublica,
    obtener_excepciones_activas,
    obtener_rango_reserva_publica,
)
from .models import (
    ConfiguracionAgendaInteligente,
    DisponibilidadOdontologo,
    TipoTurnoOdontologo,
    Turno,
)

# El puntaje persistido solo es comparable dentro de la misma versión del algoritmo.
ALGORITMO_HORARIO_VERSION = "smart-v2"
FRANJA_TARDE_HORA = 13
DISTANCIA_RECOMENDADA_MINUTOS = 60

APROVECHAMIENTO_EXACTO = "exacto_para_servicio"
APROVECHAMIENTO_SERVICIO = "admite_servicio"
APROVECHAMIENTO_GENERICO = "util_generico"
APROVECHAMIENTO_INUTIL = "inutil"
APROVECHAMIENTO_SIN_HUECO = "sin_hueco"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntervaloLibre:
    inicio: datetime
    fin: datetime
    limitado_por_ocupacion_inicio: bool = False
    limitado_por_ocupacion_fin: bool = False

    @property
    def duracion_minutos(self):
        return _minutos_entre(self.inicio, self.fin)


@dataclass(frozen=True)
class CandidatoHorario:
    hora_inicio: time
    hora_fin_atencion: time
    hora_fin_bloqueada: time
    intervalo_libre_inicio: time
    intervalo_libre_fin: time
    hueco_anterior_minutos: int
    hueco_posterior_minutos: int
    puntaje: int
    clasificacion: str
    razones_tecnicas: tuple[str, ...]


@dataclass(frozen=True)
class ResultadoHorariosInteligentes:
    recomendados: tuple[CandidatoHorario, ...]
    alternativos: tuple[CandidatoHorario, ...]
    descartados_por_fragmentacion: int
    total_candidatos_validos: int
    algoritmo_version: str = ALGORITMO_HORARIO_VERSION

    @property
    def todos(self):
        return self.recomendados + self.alternativos

    @property
    def cronologicos(self):
        """Todos los horarios ofrecidos por hora de inicio, sin separar recomendados."""
        return tuple(sorted(self.todos, key=_clave_cronologica))


def resultado_vacio(descartados_por_fragmentacion=0):
    return ResultadoHorariosInteligentes(
        recomendados=(),
        alternativos=(),
        descartados_por_fragmentacion=descartados_por_fragmentacion,
        total_candidatos_validos=0,
    )


def evaluar_aprovechamiento_hueco(minutos, duraciones_publicas, hueco_minimo):
    if minutos == 0:
        return APROVECHAMIENTO_SIN_HUECO
    if minutos < hueco_minimo:
        return APROVECHAMIENTO_INUTIL

    duraciones = sorted({int(duracion) for duracion in duraciones_publicas if duracion > 0})
    if minutos in duraciones:
        return APROVECHAMIENTO_EXACTO
    if any(duracion <= minutos for duracion in duraciones):
        return APROVECHAMIENTO_SERVICIO
    return APROVECHAMIENTO_GENERICO


def construir_intervalos_libres(disponibilidades, intervalos_ocupados):
    ocupados = _fusionar_intervalos(intervalos_ocupados)
    libres = []

    for disponibilidad_inicio, disponibilidad_fin in sorted(disponibilidades):
        cursor = disponibilidad_inicio
        limitado_inicio = False

        for ocupado_inicio, ocupado_fin in ocupados:
            inicio = max(ocupado_inicio, disponibilidad_inicio)
            fin = min(ocupado_fin, disponibilidad_fin)

            if inicio >= fin:
                continue
            if inicio > cursor:
                libres.append(
                    IntervaloLibre(
                        inicio=cursor,
                        fin=inicio,
                        limitado_por_ocupacion_inicio=limitado_inicio,
                        limitado_por_ocupacion_fin=True,
                    )
                )
            cursor = max(cursor, fin)
            limitado_inicio = True

            if cursor >= disponibilidad_fin:
                break

        if cursor < disponibilidad_fin:
            libres.append(
                IntervaloLibre(
                    inicio=cursor,
                    fin=disponibilidad_fin,
                    limitado_por_ocupacion_inicio=limitado_inicio,
                    limitado_por_ocupacion_fin=False,
                )
            )

    return tuple(libres)


def generar_resultado_horarios_inteligentes(
    *,
    intervalos_libres,
    duracion_atencion_minutos,
    margen_posterior_minutos,
    configuracion,
    duraciones_publicas,
    hora_inicio_minima=None,
):
    duracion_bloqueada = duracion_atencion_minutos + margen_posterior_minutos
    if duracion_bloqueada <= 0:
        return resultado_vacio()

    candidatos = []
    descartados = 0

    for intervalo in intervalos_libres:
        aceptados, descartados_intervalo = _candidatos_del_intervalo(
            intervalo=intervalo,
            duracion_atencion_minutos=duracion_atencion_minutos,
            duracion_bloqueada=duracion_bloqueada,
            configuracion=configuracion,
            duraciones_publicas=duraciones_publicas,
            hora_inicio_minima=hora_inicio_minima,
        )
        candidatos.extend(aceptados)
        descartados += descartados_intervalo

    recomendados, alternativos = seleccionar_horarios_diversos(candidatos, configuracion)
    return ResultadoHorariosInteligentes(
        recomendados=tuple(
            replace(candidato, clasificacion=Turno.ClasificacionHorario.RECOMENDADO)
            for candidato in recomendados
        ),
        alternativos=tuple(
            replace(candidato, clasificacion=Turno.ClasificacionHorario.ALTERNATIVO)
            for candidato in alternativos
        ),
        descartados_por_fragmentacion=descartados,
        total_candidatos_validos=len(candidatos),
    )


def seleccionar_horarios_diversos(candidatos, configuracion):
    if not configuracion.evitar_huecos:
        # Sin organizar la agenda no hay criterio para destacar: todo es una alternativa.
        return [], sorted(candidatos, key=_clave_cronologica)

    ordenados = sorted(candidatos, key=_clave_prioridad)
    limite = configuracion.cantidad_horarios_recomendados
    seleccionados = []

    manana = [
        candidato for candidato in ordenados if candidato.hora_inicio.hour < FRANJA_TARDE_HORA
    ]
    tarde = [
        candidato for candidato in ordenados if candidato.hora_inicio.hour >= FRANJA_TARDE_HORA
    ]

    if limite >= 2 and manana and tarde:
        primeras_franjas = sorted((manana[0], tarde[0]), key=_clave_prioridad)
        seleccionados.extend(primeras_franjas)

    for candidato in ordenados:
        if len(seleccionados) >= limite:
            break
        if candidato in seleccionados:
            continue
        if all(
            _distancia_horarios_minutos(candidato, elegido) >= DISTANCIA_RECOMENDADA_MINUTOS
            for elegido in seleccionados
        ):
            seleccionados.append(candidato)

    for candidato in ordenados:
        if len(seleccionados) >= limite:
            break
        if candidato not in seleccionados:
            seleccionados.append(candidato)

    # Los recomendados son un destacado, no un filtro: todo candidato válido restante se ofrece.
    # Van en orden cronológico para que la grilla Mañana/Tarde sea legible. El trabajo está
    # acotado por construcción (una grilla por intervalo libre más dos anclas), por eso
    # `cantidad_horarios_alternativos` ya no limita el resultado desde smart-v2.
    seleccionados_set = set(seleccionados)
    alternativos = sorted(
        (candidato for candidato in ordenados if candidato not in seleccionados_set),
        key=_clave_cronologica,
    )
    return seleccionados, alternativos


@dataclass(frozen=True)
class ContextoAgenda:
    """Agenda de un odontólogo para un rango de fechas, cargada en bloque.

    Separa la carga (consultas) del cálculo por día (memoria pura): así se puede evaluar un rango
    completo con una cantidad de consultas constante en lugar de repetirlas por cada día.
    """

    configuracion: ConfiguracionAgendaInteligente
    rango: RangoReservaPublica
    disponibilidades_por_dia: dict
    turnos_por_fecha: dict
    excepciones: tuple
    duraciones_bloqueadas: tuple


def cargar_contexto_agenda(
    *,
    odontologo,
    fecha_desde,
    fecha_hasta,
    rango,
    turno_excluido=None,
    configuracion=None,
):
    """Carga la agenda del rango. Devuelve None si no hay nada que ofrecer."""
    if configuracion is None:
        configuracion, _ = ConfiguracionAgendaInteligente.objects.get_or_create(
            odontologo=odontologo
        )
    if not configuracion.activa:
        return None

    dias_semana = {
        (fecha_desde + timedelta(days=desplazamiento)).weekday()
        for desplazamiento in range(min((fecha_hasta - fecha_desde).days + 1, 7))
    }
    disponibilidades_por_dia = {}
    disponibilidades = (
        DisponibilidadOdontologo.objects.filter(
            odontologo=odontologo,
            dia_semana__in=dias_semana,
            activo=True,
        )
        .order_by("hora_inicio")
        .values_list("dia_semana", "hora_inicio", "hora_fin")
    )
    for dia_semana, hora_inicio, hora_fin in disponibilidades:
        disponibilidades_por_dia.setdefault(dia_semana, []).append((hora_inicio, hora_fin))
    if not disponibilidades_por_dia:
        return None

    turnos = Turno.objects.filter(
        odontologo=odontologo,
        fecha__range=(fecha_desde, fecha_hasta),
        estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
    ).only("id", "fecha", "hora_inicio", "duracion_minutos")
    if turno_excluido and turno_excluido.pk:
        turnos = turnos.exclude(pk=turno_excluido.pk)
    turnos_por_fecha = {}
    for turno in turnos:
        turnos_por_fecha.setdefault(turno.fecha, []).append(
            (turno.fecha_hora_inicio, turno.fecha_hora_fin)
        )

    duraciones_publicas = TipoTurnoOdontologo.objects.filter(
        odontologo=odontologo,
        activo=True,
        reserva_publica=True,
        tipo_turno__activo=True,
        tipo_turno__visible_publicamente=True,
    ).values_list("duracion_atencion_minutos", "margen_posterior_minutos")

    return ContextoAgenda(
        configuracion=configuracion,
        rango=rango,
        disponibilidades_por_dia=disponibilidades_por_dia,
        turnos_por_fecha=turnos_por_fecha,
        excepciones=tuple(obtener_excepciones_activas(odontologo, fecha_desde, fecha_hasta)),
        duraciones_bloqueadas=tuple(atencion + margen for atencion, margen in duraciones_publicas),
    )


def calcular_horarios_del_dia(
    contexto,
    fecha,
    duracion_atencion_minutos,
    margen_posterior_minutos=0,
):
    """Calcula los horarios de un día a partir de un contexto ya cargado (sin consultas)."""
    disponibilidades = contexto.disponibilidades_por_dia.get(fecha.weekday())
    if not disponibilidades:
        return resultado_vacio()

    intervalos_disponibles = [
        (datetime.combine(fecha, inicio), datetime.combine(fecha, fin))
        for inicio, fin in disponibilidades
    ]
    intervalos_ocupados = list(contexto.turnos_por_fecha.get(fecha, ()))
    intervalos_ocupados.extend(_intervalos_excepciones(fecha, contexto.excepciones))
    intervalos_libres = construir_intervalos_libres(
        intervalos_disponibles,
        intervalos_ocupados,
    )
    hora_inicio_minima = None
    if fecha == contexto.rango.fecha_hora_minima.date():
        hora_inicio_minima = (
            timezone.localtime(contexto.rango.fecha_hora_minima).replace(tzinfo=None).time()
        )
    return generar_resultado_horarios_inteligentes(
        intervalos_libres=intervalos_libres,
        duracion_atencion_minutos=duracion_atencion_minutos,
        margen_posterior_minutos=margen_posterior_minutos,
        configuracion=contexto.configuracion,
        duraciones_publicas=contexto.duraciones_bloqueadas,
        hora_inicio_minima=hora_inicio_minima,
    )


def calcular_horarios_inteligentes(
    *,
    odontologo,
    fecha,
    duracion_atencion_minutos,
    margen_posterior_minutos=0,
    turno_excluido=None,
    ahora=None,
    configuracion=None,
):
    inicio_calculo = perf_counter()
    rango = obtener_rango_reserva_publica(ahora)
    if not odontologo.activo or not rango.fecha_minima <= fecha <= rango.fecha_maxima:
        return resultado_vacio()

    contexto = cargar_contexto_agenda(
        odontologo=odontologo,
        fecha_desde=fecha,
        fecha_hasta=fecha,
        rango=rango,
        turno_excluido=turno_excluido,
        configuracion=configuracion,
    )
    if contexto is None:
        return resultado_vacio()

    resultado = calcular_horarios_del_dia(
        contexto,
        fecha,
        duracion_atencion_minutos,
        margen_posterior_minutos,
    )
    logger.info(
        (
            "Agenda inteligente calculada. odontologo_id=%s fecha=%s cantidad_candidatos=%s "
            "cantidad_recomendados=%s cantidad_alternativos=%s "
            "cantidad_descartados_fragmentacion=%s duracion_ms=%s algoritmo_version=%s"
        ),
        odontologo.pk,
        fecha.isoformat(),
        resultado.total_candidatos_validos,
        len(resultado.recomendados),
        len(resultado.alternativos),
        resultado.descartados_por_fragmentacion,
        round((perf_counter() - inicio_calculo) * 1000),
        resultado.algoritmo_version,
    )
    return resultado


def buscar_proximo_horario_disponible(
    *,
    odontologo,
    desde,
    duracion_atencion_minutos,
    margen_posterior_minutos=0,
    ahora=None,
    configuracion=None,
):
    """Primer horario libre posterior a `desde` dentro de la ventana pública de reserva.

    Devuelve `(fecha, candidato)` o None. Carga la agenda de toda la ventana en bloque (cantidad
    de consultas constante) y evalúa cada día en memoria con el mismo motor que la reserva.
    Es solo informativo: al reservar se vuelve a validar bajo bloqueo y sin caché.
    """
    rango = obtener_rango_reserva_publica(ahora)
    primera_fecha = max(desde + timedelta(days=1), rango.fecha_minima)
    if not odontologo.activo or primera_fecha > rango.fecha_maxima:
        return None

    contexto = cargar_contexto_agenda(
        odontologo=odontologo,
        fecha_desde=primera_fecha,
        fecha_hasta=rango.fecha_maxima,
        rango=rango,
        configuracion=configuracion,
    )
    if contexto is None:
        return None

    for desplazamiento in range((rango.fecha_maxima - primera_fecha).days + 1):
        fecha = primera_fecha + timedelta(days=desplazamiento)
        resultado = calcular_horarios_del_dia(
            contexto,
            fecha,
            duracion_atencion_minutos,
            margen_posterior_minutos,
        )
        if resultado.todos:
            return fecha, resultado.cronologicos[0]
    return None


def buscar_candidato(resultado, hora_inicio):
    return next(
        (candidato for candidato in resultado.todos if candidato.hora_inicio == hora_inicio),
        None,
    )


def _candidatos_del_intervalo(
    *,
    intervalo,
    duracion_atencion_minutos,
    duracion_bloqueada,
    configuracion,
    duraciones_publicas,
    hora_inicio_minima,
):
    """Devuelve (candidatos aceptados, cantidad descartada por fragmentación) de un intervalo.

    La regla antifragmentación es una preferencia: si todos los candidatos del intervalo
    dejarían un fragmento inútil pero el servicio entra, se rescata el mejor candidato pegado
    a un borde para no dejar tiempo inutilizable.
    """
    bloque = timedelta(minutes=duracion_bloqueada)
    validos = []
    fragmentadores = []
    organizar = configuracion.evitar_huecos

    for inicio in _inicios_candidatos(
        intervalo,
        duracion_bloqueada,
        configuracion.intervalo_inicio_minutos,
        hora_inicio_minima,
        con_anclas=organizar,
    ):
        fin_bloqueado = inicio + bloque
        hueco_anterior = _minutos_entre(intervalo.inicio, inicio)
        hueco_posterior = _minutos_entre(fin_bloqueado, intervalo.fin)
        if organizar:
            fragmenta = _deja_fragmento_inutil(
                hueco_anterior,
                hueco_posterior,
                configuracion.hueco_minimo_util_minutos,
            )
            puntaje, razones = _criterios_candidato(
                intervalo=intervalo,
                hueco_anterior=hueco_anterior,
                hueco_posterior=hueco_posterior,
                fragmenta=fragmenta,
                duraciones_publicas=duraciones_publicas,
                configuracion=configuracion,
            )
        else:
            # Sin organizar la agenda: grilla pura, sin preferencias ni fallback.
            fragmenta = False
            puntaje, razones = 0, ["grilla_simple"]
        candidato = CandidatoHorario(
            hora_inicio=inicio.time(),
            hora_fin_atencion=(inicio + timedelta(minutes=duracion_atencion_minutos)).time(),
            hora_fin_bloqueada=fin_bloqueado.time(),
            intervalo_libre_inicio=intervalo.inicio.time(),
            intervalo_libre_fin=intervalo.fin.time(),
            hueco_anterior_minutos=hueco_anterior,
            hueco_posterior_minutos=hueco_posterior,
            puntaje=puntaje,
            clasificacion="",
            razones_tecnicas=tuple(razones),
        )
        (fragmentadores if fragmenta else validos).append(candidato)

    if validos:
        return validos, len(fragmentadores)

    pegados_a_borde = [
        candidato
        for candidato in fragmentadores
        if candidato.hueco_anterior_minutos == 0 or candidato.hueco_posterior_minutos == 0
    ]
    if not pegados_a_borde:
        return [], len(fragmentadores)
    return [min(pegados_a_borde, key=_clave_prioridad)], len(fragmentadores) - 1


def _inicios_candidatos(
    intervalo,
    duracion_bloqueada,
    grilla_minutos,
    hora_inicio_minima,
    *,
    con_anclas=True,
):
    """Inicios posibles: la grilla más el inicio del intervalo y el último inicio que entra.

    Las anclas se redondean a minutos enteros para que el horario ofrecido (HH:MM) sea el mismo
    que se revalida al guardar.
    """
    bloque = timedelta(minutes=duracion_bloqueada)
    inicios = set()

    cursor = _alinear_datetime(intervalo.inicio, grilla_minutos)
    while cursor + bloque <= intervalo.fin:
        inicios.add(cursor)
        cursor += timedelta(minutes=grilla_minutos)

    if con_anclas:
        ancla_inicio = _alinear_datetime(intervalo.inicio, 1)
        ancla_fin = (intervalo.fin - bloque).replace(second=0, microsecond=0)
        for ancla in (ancla_inicio, ancla_fin):
            if ancla >= intervalo.inicio and ancla + bloque <= intervalo.fin:
                inicios.add(ancla)

    return sorted(
        inicio
        for inicio in inicios
        if hora_inicio_minima is None or inicio.time() >= hora_inicio_minima
    )


def _criterios_candidato(
    *,
    intervalo,
    hueco_anterior,
    hueco_posterior,
    fragmenta,
    duraciones_publicas,
    configuracion,
):
    """Orden lexicográfico smart-v2 codificado en un entero de seis dígitos.

    Cada dígito es un criterio (mayor es mejor) y el primero domina a los siguientes:

        a  ocupa el intervalo libre exacto                       0/1
        b  no deja un hueco inútil (0 solo en el fallback)       0/1
        c  pegado a un turno o bloqueo (2) o al borde de la
           disponibilidad (1)                                    0/1/2
        d  no rompe un bloque largo (si se preserva)             0/1
        e  el resto sirve para otro servicio publicado
           (exacto 2, admite 1)                                  0/1/2
        f  coincide con el modo de compactación                  0/1

    Ordenar por puntaje descendente equivale a comparar la tupla (a, b, c, d, e, f). El
    desempate final es la hora de inicio más temprana (ver `_clave_prioridad`).
    """
    razones = []
    pegado_inicio = hueco_anterior == 0
    pegado_final = hueco_posterior == 0

    ocupa_exacto = pegado_inicio and pegado_final
    if ocupa_exacto:
        razones.append("ocupa_intervalo_exacto")

    if fragmenta:
        razones.append("fallback_fragmentacion")

    pegado = 0
    if pegado_inicio:
        razones.append("pegado_inicio")
        pegado = max(pegado, 2 if intervalo.limitado_por_ocupacion_inicio else 1)
    if pegado_final:
        razones.append("pegado_final")
        pegado = max(pegado, 2 if intervalo.limitado_por_ocupacion_fin else 1)
    if pegado == 2:
        razones.append("pegado_a_turno")
    elif pegado == 1:
        razones.append("pegado_a_borde_disponibilidad")

    conserva_bloque = True
    if configuracion.preservar_bloques_largos:
        bloque_largo = intervalo.duracion_minutos >= configuracion.bloque_largo_minutos
        resto_mayor = max(hueco_anterior, hueco_posterior)
        conserva_bloque = not bloque_largo or resto_mayor >= configuracion.bloque_largo_minutos
        razones.append("conserva_bloque_largo" if conserva_bloque else "rompe_bloque_largo")

    evaluaciones = {
        evaluar_aprovechamiento_hueco(
            minutos,
            duraciones_publicas,
            configuracion.hueco_minimo_util_minutos,
        )
        for minutos in (hueco_anterior, hueco_posterior)
    }
    aprovechamiento = 0
    if APROVECHAMIENTO_EXACTO in evaluaciones:
        aprovechamiento = 2
        razones.append("resto_exacto_para_servicio")
    elif APROVECHAMIENTO_SERVICIO in evaluaciones:
        aprovechamiento = 1
        razones.append("resto_admite_servicio")

    modo = configuracion.modo_compactacion
    coincide_modo = (
        modo == ConfiguracionAgendaInteligente.ModoCompactacion.INICIO and pegado_inicio
    ) or (modo == ConfiguracionAgendaInteligente.ModoCompactacion.FINAL and pegado_final)
    if coincide_modo:
        razones.append(
            "modo_inicio"
            if modo == ConfiguracionAgendaInteligente.ModoCompactacion.INICIO
            else "modo_final"
        )

    criterios = (
        int(ocupa_exacto),
        int(not fragmenta),
        pegado,
        int(conserva_bloque),
        aprovechamiento,
        int(coincide_modo),
    )
    puntaje = 0
    for criterio in criterios:
        puntaje = puntaje * 10 + criterio
    return puntaje, razones


def _intervalos_excepciones(fecha, excepciones):
    intervalos = []
    for excepcion in excepciones:
        if not excepcion.fecha_desde <= fecha <= excepcion.fecha_hasta:
            continue
        if excepcion.todo_el_dia:
            intervalos.append(
                (
                    datetime.combine(fecha, time.min),
                    datetime.combine(fecha + timedelta(days=1), time.min),
                )
            )
        elif excepcion.hora_inicio and excepcion.hora_fin:
            intervalos.append(
                (
                    datetime.combine(fecha, excepcion.hora_inicio),
                    datetime.combine(fecha, excepcion.hora_fin),
                )
            )
    return intervalos


def _fusionar_intervalos(intervalos):
    fusionados = []
    for inicio, fin in sorted(intervalos):
        if inicio >= fin:
            continue
        if not fusionados or inicio > fusionados[-1][1]:
            fusionados.append([inicio, fin])
            continue
        fusionados[-1][1] = max(fusionados[-1][1], fin)
    return tuple((inicio, fin) for inicio, fin in fusionados)


def _alinear_datetime(valor, intervalo_minutos):
    minutos_dia = valor.hour * 60 + valor.minute
    if valor.second or valor.microsecond:
        minutos_dia += 1
    resto = minutos_dia % intervalo_minutos
    minutos_alineados = minutos_dia if resto == 0 else minutos_dia + intervalo_minutos - resto
    return datetime.combine(valor.date(), time.min) + timedelta(minutes=minutos_alineados)


def _deja_fragmento_inutil(hueco_anterior, hueco_posterior, hueco_minimo):
    return any(0 < hueco < hueco_minimo for hueco in (hueco_anterior, hueco_posterior))


def _minutos_entre(inicio, fin):
    return int((fin - inicio).total_seconds() // 60)


def _minutos_desde_medianoche(valor):
    return valor.hour * 60 + valor.minute


def _distancia_horarios_minutos(primero, segundo):
    return abs(
        _minutos_desde_medianoche(primero.hora_inicio)
        - _minutos_desde_medianoche(segundo.hora_inicio)
    )


def _clave_prioridad(candidato):
    return (
        -candidato.puntaje,
        _minutos_desde_medianoche(candidato.hora_inicio),
        _minutos_desde_medianoche(candidato.hora_fin_bloqueada),
    )


def _clave_cronologica(candidato):
    return (
        _minutos_desde_medianoche(candidato.hora_inicio),
        _minutos_desde_medianoche(candidato.hora_fin_bloqueada),
    )
