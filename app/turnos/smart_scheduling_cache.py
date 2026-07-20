import logging
from dataclasses import asdict

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_time

from .models import ConfiguracionAgendaInteligente
from .smart_scheduling import (
    ALGORITMO_HORARIO_VERSION,
    CandidatoHorario,
    ResultadoHorariosInteligentes,
    calcular_horarios_inteligentes,
)

logger = logging.getLogger(__name__)


def obtener_horarios_inteligentes_cacheados(*, configuracion_tipo, fecha, ahora=None):
    configuracion_agenda, _ = ConfiguracionAgendaInteligente.objects.get_or_create(
        odontologo=configuracion_tipo.odontologo
    )
    try:
        ttl = max(0, int(settings.TURNOS_PUBLIC_BOOKING_HORARIOS_CACHE_SECONDS))
    except (TypeError, ValueError):
        ttl = 60
    momento = timezone.localtime(ahora or timezone.now())

    if ttl <= 0:
        return (
            _calcular(configuracion_tipo, configuracion_agenda, fecha, ahora),
            False,
        )

    bucket = int(momento.timestamp() // ttl)
    clave = _construir_clave(
        configuracion_tipo=configuracion_tipo,
        configuracion_agenda=configuracion_agenda,
        fecha=fecha,
        bucket=bucket,
    )

    try:
        guardado = cache.get(clave)
    except Exception as error:
        _registrar_warning_cache("get", error)
        guardado = None

    if guardado is not None:
        try:
            return _deserializar_resultado(guardado), True
        except Exception as error:
            # La caché es una optimización; un valor incompleto no debe bloquear horarios reales.
            _registrar_warning_cache("deserialize", error)

    resultado = _calcular(configuracion_tipo, configuracion_agenda, fecha, ahora)
    try:
        cache.set(clave, _serializar_resultado(resultado), ttl)
    except Exception as error:
        _registrar_warning_cache("set", error)
    return resultado, False


def _calcular(configuracion_tipo, configuracion_agenda, fecha, ahora):
    return calcular_horarios_inteligentes(
        odontologo=configuracion_tipo.odontologo,
        fecha=fecha,
        duracion_atencion_minutos=configuracion_tipo.duracion_atencion_minutos,
        margen_posterior_minutos=configuracion_tipo.margen_posterior_minutos,
        configuracion=configuracion_agenda,
        ahora=ahora,
    )


def _construir_clave(*, configuracion_tipo, configuracion_agenda, fecha, bucket):
    tipo_actualizado = int(configuracion_tipo.tipo_turno.actualizado_en.timestamp())
    servicio_actualizado = int(configuracion_tipo.actualizado_en.timestamp())
    agenda_actualizada = int(configuracion_agenda.actualizado_en.timestamp())
    return (
        "turnos:public_booking:smart:v1:"
        f"{configuracion_tipo.odontologo_id}:{fecha.isoformat()}:{configuracion_tipo.pk}:"
        f"{tipo_actualizado}:{servicio_actualizado}:{agenda_actualizada}:"
        f"{ALGORITMO_HORARIO_VERSION}:{bucket}"
    )


def _serializar_resultado(resultado):
    return {
        "recomendados": [_serializar_candidato(item) for item in resultado.recomendados],
        "alternativos": [_serializar_candidato(item) for item in resultado.alternativos],
        "descartados_por_fragmentacion": resultado.descartados_por_fragmentacion,
        "total_candidatos_validos": resultado.total_candidatos_validos,
        "algoritmo_version": resultado.algoritmo_version,
    }


def _serializar_candidato(candidato):
    datos = asdict(candidato)
    datos.pop("razones_tecnicas", None)
    for campo in (
        "hora_inicio",
        "hora_fin_atencion",
        "hora_fin_bloqueada",
        "intervalo_libre_inicio",
        "intervalo_libre_fin",
    ):
        datos[campo] = datos[campo].strftime("%H:%M:%S")
    return datos


def _deserializar_resultado(datos):
    return ResultadoHorariosInteligentes(
        recomendados=tuple(_deserializar_candidato(item) for item in datos["recomendados"]),
        alternativos=tuple(_deserializar_candidato(item) for item in datos["alternativos"]),
        descartados_por_fragmentacion=datos["descartados_por_fragmentacion"],
        total_candidatos_validos=datos["total_candidatos_validos"],
        algoritmo_version=datos.get("algoritmo_version", ALGORITMO_HORARIO_VERSION),
    )


def _deserializar_candidato(datos):
    valores = dict(datos)
    for campo in (
        "hora_inicio",
        "hora_fin_atencion",
        "hora_fin_bloqueada",
        "intervalo_libre_inicio",
        "intervalo_libre_fin",
    ):
        valores[campo] = parse_time(valores[campo])
        if valores[campo] is None:
            raise ValueError("Horario cacheado inválido.")
    valores["razones_tecnicas"] = ()
    return CandidatoHorario(**valores)


def _registrar_warning_cache(etapa, error):
    logger.warning(
        "No se pudo usar cache de agenda inteligente. etapa=%s cache=%s error_type=%s",
        etapa,
        cache.__class__.__name__,
        error.__class__.__name__,
    )
