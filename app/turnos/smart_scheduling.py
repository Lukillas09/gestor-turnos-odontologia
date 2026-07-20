import logging
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from time import perf_counter

from django.utils import timezone

from .excepciones import (
    obtener_excepciones_activas,
    obtener_rango_reserva_publica,
)
from .models import (
    ConfiguracionAgendaInteligente,
    DisponibilidadOdontologo,
    TipoTurnoOdontologo,
    Turno,
)

ALGORITMO_HORARIO_VERSION = "smart-v1"
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

    cantidad_bloques_largos = sum(
        intervalo.duracion_minutos >= configuracion.bloque_largo_minutos
        for intervalo in intervalos_libres
    )
    candidatos = []
    descartados = 0

    for intervalo in intervalos_libres:
        inicio = _alinear_datetime(
            intervalo.inicio,
            configuracion.intervalo_inicio_minutos,
        )

        while inicio + timedelta(minutes=duracion_bloqueada) <= intervalo.fin:
            fin_atencion = inicio + timedelta(minutes=duracion_atencion_minutos)
            fin_bloqueado = inicio + timedelta(minutes=duracion_bloqueada)

            if hora_inicio_minima and inicio.time() < hora_inicio_minima:
                inicio += timedelta(minutes=configuracion.intervalo_inicio_minutos)
                continue

            hueco_anterior = _minutos_entre(intervalo.inicio, inicio)
            hueco_posterior = _minutos_entre(fin_bloqueado, intervalo.fin)

            if _deja_fragmento_inutil(
                hueco_anterior,
                hueco_posterior,
                configuracion.hueco_minimo_util_minutos,
            ):
                descartados += 1
            else:
                puntaje, razones = _puntuar_candidato(
                    intervalo=intervalo,
                    hueco_anterior=hueco_anterior,
                    hueco_posterior=hueco_posterior,
                    duraciones_publicas=duraciones_publicas,
                    configuracion=configuracion,
                    cantidad_bloques_largos=cantidad_bloques_largos,
                )
                candidatos.append(
                    CandidatoHorario(
                        hora_inicio=inicio.time(),
                        hora_fin_atencion=fin_atencion.time(),
                        hora_fin_bloqueada=fin_bloqueado.time(),
                        intervalo_libre_inicio=intervalo.inicio.time(),
                        intervalo_libre_fin=intervalo.fin.time(),
                        hueco_anterior_minutos=hueco_anterior,
                        hueco_posterior_minutos=hueco_posterior,
                        puntaje=puntaje,
                        clasificacion="",
                        razones_tecnicas=tuple(razones),
                    )
                )

            inicio += timedelta(minutes=configuracion.intervalo_inicio_minutos)

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

    seleccionados_set = set(seleccionados)
    alternativos = [candidato for candidato in ordenados if candidato not in seleccionados_set][
        : configuracion.cantidad_horarios_alternativos
    ]
    return seleccionados, alternativos


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

    if configuracion is None:
        configuracion, _ = ConfiguracionAgendaInteligente.objects.get_or_create(
            odontologo=odontologo
        )
    if not configuracion.activa:
        return resultado_vacio()

    disponibilidades = list(
        DisponibilidadOdontologo.objects.filter(
            odontologo=odontologo,
            dia_semana=fecha.weekday(),
            activo=True,
        )
        .order_by("hora_inicio")
        .values_list("hora_inicio", "hora_fin")
    )
    if not disponibilidades:
        return resultado_vacio()

    turnos = Turno.objects.filter(
        odontologo=odontologo,
        fecha=fecha,
        estado__in=[Turno.Estado.PENDIENTE, Turno.Estado.CONFIRMADO],
    ).only("id", "fecha", "hora_inicio", "duracion_minutos")
    if turno_excluido and turno_excluido.pk:
        turnos = turnos.exclude(pk=turno_excluido.pk)

    excepciones = list(obtener_excepciones_activas(odontologo, fecha, fecha))
    intervalos_disponibles = [
        (datetime.combine(fecha, inicio), datetime.combine(fecha, fin))
        for inicio, fin in disponibilidades
    ]
    intervalos_ocupados = [(turno.fecha_hora_inicio, turno.fecha_hora_fin) for turno in turnos]
    intervalos_ocupados.extend(_intervalos_excepciones(fecha, excepciones))
    intervalos_libres = construir_intervalos_libres(
        intervalos_disponibles,
        intervalos_ocupados,
    )
    duraciones_publicas = list(
        TipoTurnoOdontologo.objects.filter(
            odontologo=odontologo,
            activo=True,
            reserva_publica=True,
            tipo_turno__activo=True,
            tipo_turno__visible_publicamente=True,
        ).values_list("duracion_atencion_minutos", "margen_posterior_minutos")
    )
    duraciones_bloqueadas = [atencion + margen for atencion, margen in duraciones_publicas]
    hora_inicio_minima = None
    if fecha == rango.fecha_hora_minima.date():
        hora_inicio_minima = timezone.localtime(rango.fecha_hora_minima).replace(tzinfo=None).time()
    resultado = generar_resultado_horarios_inteligentes(
        intervalos_libres=intervalos_libres,
        duracion_atencion_minutos=duracion_atencion_minutos,
        margen_posterior_minutos=margen_posterior_minutos,
        configuracion=configuracion,
        duraciones_publicas=duraciones_bloqueadas,
        hora_inicio_minima=hora_inicio_minima,
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


def buscar_candidato(resultado, hora_inicio):
    return next(
        (candidato for candidato in resultado.todos if candidato.hora_inicio == hora_inicio),
        None,
    )


def _puntuar_candidato(
    *,
    intervalo,
    hueco_anterior,
    hueco_posterior,
    duraciones_publicas,
    configuracion,
    cantidad_bloques_largos,
):
    puntaje = 0
    razones = []
    exacto = hueco_anterior == 0 and hueco_posterior == 0
    divide = hueco_anterior > 0 and hueco_posterior > 0

    if exacto:
        puntaje += 1000
        razones.append("ocupa_intervalo_exacto")
        if intervalo.limitado_por_ocupacion_inicio and intervalo.limitado_por_ocupacion_fin:
            puntaje += 220
            razones.append("completa_hueco_entre_turnos")
    if hueco_anterior == 0:
        puntaje += 350
        razones.append("pegado_inicio")
    if hueco_posterior == 0:
        puntaje += 350
        razones.append("pegado_final")

    evaluaciones = {
        evaluar_aprovechamiento_hueco(
            minutos,
            duraciones_publicas,
            configuracion.hueco_minimo_util_minutos,
        )
        for minutos in (hueco_anterior, hueco_posterior)
    }
    if APROVECHAMIENTO_EXACTO in evaluaciones:
        puntaje += 180
        razones.append("resto_exacto_para_servicio")
    elif APROVECHAMIENTO_SERVICIO in evaluaciones:
        puntaje += 100
        razones.append("resto_admite_servicio")

    if divide:
        puntaje -= 150
        razones.append("divide_intervalo")
    else:
        puntaje += 40
        razones.append("evita_division")

    if configuracion.preservar_bloques_largos:
        bloque_largo = intervalo.duracion_minutos >= configuracion.bloque_largo_minutos
        resto_mayor = max(hueco_anterior, hueco_posterior)
        conserva_bloque = resto_mayor >= configuracion.bloque_largo_minutos

        if bloque_largo and conserva_bloque:
            puntaje += 60
            razones.append("conserva_bloque_largo")
        if bloque_largo and cantidad_bloques_largos == 1 and not conserva_bloque:
            puntaje -= 220
            razones.append("reduce_unico_bloque_largo")
        if bloque_largo and divide and not conserva_bloque:
            puntaje -= 300
            razones.append("fragmenta_bloque_largo")

    if (
        configuracion.modo_compactacion == ConfiguracionAgendaInteligente.ModoCompactacion.INICIO
        and hueco_anterior == 0
    ):
        puntaje += 80
        razones.append("modo_inicio")
    elif (
        configuracion.modo_compactacion == ConfiguracionAgendaInteligente.ModoCompactacion.FINAL
        and hueco_posterior == 0
    ):
        puntaje += 80
        razones.append("modo_final")

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
