import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from turnos.models import LimitePublico

from .exceptions import ProteccionPublicaNoDisponible

logger = logging.getLogger(__name__)

CACHE_PREFIX = "turnos:public_access"
MAX_AMBITO_LENGTH = int(LimitePublico._meta.get_field("ambito").max_length or 0)
MAX_SUJETO_HASH_LENGTH = int(LimitePublico._meta.get_field("sujeto_hash").max_length or 0)


@dataclass(frozen=True)
class ResultadoRateLimit:
    permitido: bool
    contador: int
    limite: int
    identificador_ventana: str

    @property
    def cache_key(self):
        return self.identificador_ventana


def incrementar_limite(nombre, sujeto_hash, limite, ventana_segundos, ahora=None):
    _validar_identidad(nombre, sujeto_hash)

    if limite < 0:
        raise ValueError("El límite no puede ser negativo.")

    if limite == 0:
        return ResultadoRateLimit(
            permitido=True,
            contador=0,
            limite=limite,
            identificador_ventana=f"{CACHE_PREFIX}:{nombre}:disabled",
        )

    momento = _normalizar_ahora(ahora)
    ventana_inicio, expira_en = calcular_ventana_fija(momento, ventana_segundos)
    identificador = _identificador_ventana(nombre, sujeto_hash, ventana_inicio)

    try:
        contador = _incrementar_contador_transaccional(
            nombre=nombre,
            sujeto_hash=sujeto_hash,
            ventana_inicio=ventana_inicio,
            expira_en=expira_en,
            ahora=momento,
        )
    except DatabaseError as error:
        _registrar_fallo_db("rate_limit_increment", error)
        raise ProteccionPublicaNoDisponible() from error

    return ResultadoRateLimit(
        permitido=contador <= limite,
        contador=contador,
        limite=limite,
        identificador_ventana=identificador,
    )


def leer_contador(nombre, sujeto_hash, ventana_segundos, ahora=None):
    _validar_identidad(nombre, sujeto_hash)
    momento = _normalizar_ahora(ahora)
    ventana_inicio, _expira_en = calcular_ventana_fija(momento, ventana_segundos)

    try:
        contador = (
            LimitePublico.objects.filter(
                ambito=nombre,
                sujeto_hash=sujeto_hash,
                ventana_inicio=ventana_inicio,
                expira_en__gt=momento,
            )
            .values_list("contador", flat=True)
            .first()
        )
    except DatabaseError as error:
        _registrar_fallo_db("rate_limit_read", error)
        raise ProteccionPublicaNoDisponible() from error

    return int(contador or 0)


def calcular_ventana_fija(ahora, ventana_segundos):
    if ventana_segundos <= 0:
        raise ValueError("La ventana debe ser mayor a cero.")

    momento = _normalizar_ahora(ahora)
    epoch = int(momento.timestamp())
    inicio_epoch = epoch - (epoch % ventana_segundos)
    ventana_inicio = datetime.fromtimestamp(inicio_epoch, tz=UTC)
    expira_en = ventana_inicio + timedelta(seconds=ventana_segundos)
    return ventana_inicio, expira_en


def construir_clave(nombre, sujeto_hash):
    return f"{CACHE_PREFIX}:{nombre}:{sujeto_hash}"


def _incrementar_contador_transaccional(
    *,
    nombre,
    sujeto_hash,
    ventana_inicio,
    expira_en,
    ahora,
):
    lookup = {
        "ambito": nombre,
        "sujeto_hash": sujeto_hash,
        "ventana_inicio": ventana_inicio,
    }

    with transaction.atomic():
        limite = LimitePublico.objects.select_for_update().filter(**lookup).first()

        if limite is not None:
            return _incrementar_fila(limite, expira_en=expira_en, ahora=ahora)

        try:
            with transaction.atomic():
                LimitePublico.objects.create(
                    **lookup,
                    contador=1,
                    expira_en=expira_en,
                )
            return 1
        except IntegrityError:
            limite = LimitePublico.objects.select_for_update().get(**lookup)
            return _incrementar_fila(limite, expira_en=expira_en, ahora=ahora)


def _incrementar_fila(limite, *, expira_en, ahora):
    LimitePublico.objects.filter(pk=limite.pk).update(
        contador=F("contador") + 1,
        expira_en=expira_en,
        actualizado_en=ahora,
    )
    limite.refresh_from_db(fields=["contador"])
    return limite.contador


def _normalizar_ahora(ahora):
    momento = ahora or timezone.now()

    if timezone.is_naive(momento):
        raise ValueError("El instante debe incluir zona horaria.")

    return momento.astimezone(UTC)


def _validar_identidad(nombre, sujeto_hash):
    if not nombre or not str(nombre).strip():
        raise ValueError("El ámbito no puede estar vacío.")

    if not sujeto_hash or not str(sujeto_hash).strip():
        raise ValueError("El hash del sujeto no puede estar vacío.")

    if len(nombre) > MAX_AMBITO_LENGTH:
        raise ValueError("El ámbito supera la longitud permitida.")

    if len(sujeto_hash) > MAX_SUJETO_HASH_LENGTH:
        raise ValueError("El hash del sujeto supera la longitud permitida.")


def _identificador_ventana(nombre, sujeto_hash, ventana_inicio):
    return f"{construir_clave(nombre, sujeto_hash)}:{int(ventana_inicio.timestamp())}"


def _registrar_fallo_db(operacion, error):
    logger.warning(
        "Protección pública no disponible. operation=%s error_type=%s",
        operacion,
        error.__class__.__name__,
    )
