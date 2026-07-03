from dataclasses import dataclass

from django.core.cache import cache


CACHE_PREFIX = "turnos:public_access"


@dataclass(frozen=True)
class ResultadoRateLimit:
    permitido: bool
    contador: int
    limite: int
    cache_key: str


def incrementar_limite(nombre, sujeto_hash, limite, ventana_segundos):
    cache_key = construir_clave(nombre, sujeto_hash)

    if limite <= 0:
        return ResultadoRateLimit(True, 0, limite, cache_key)

    agregado = cache.add(cache_key, 1, timeout=ventana_segundos)

    if agregado:
        return ResultadoRateLimit(True, 1, limite, cache_key)

    try:
        contador = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, timeout=ventana_segundos)
        contador = 1

    return ResultadoRateLimit(contador <= limite, contador, limite, cache_key)


def leer_contador(nombre, sujeto_hash):
    return int(cache.get(construir_clave(nombre, sujeto_hash), 0) or 0)


def construir_clave(nombre, sujeto_hash):
    return f"{CACHE_PREFIX}:{nombre}:{sujeto_hash}"
