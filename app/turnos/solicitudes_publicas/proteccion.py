import logging
from dataclasses import dataclass
from secrets import token_urlsafe

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from pacientes.normalizacion import normalizar_documento
from turnos.integrations.turnstile import validar_turnstile
from turnos.public_access.rate_limit import incrementar_limite, leer_contador
from turnos.public_access.tokens import hash_valor_publico, obtener_ip_cliente

logger = logging.getLogger(__name__)

SESSION_IDEMPOTENCY_KEY = "turnos_public_booking_idempotency_tokens"

RATE_LIMIT_IP_NAME = "reserva_creacion_ip"
RATE_LIMIT_DNI_NAME = "reserva_creacion_dni"
IDEMPOTENCY_CACHE_NAME = "reserva_idempotencia"

MENSAJE_LIMITE_SOLICITUD_PUBLICA = (
    "No pudimos registrar otra solicitud en este momento. Esperá unos minutos, "
    "consultá tus turnos o contactá al consultorio."
)
MENSAJE_TURNSTILE_SOLICITUD_PUBLICA = "Completá la verificación de seguridad para continuar."
MENSAJE_PROTECCION_NO_DISPONIBLE = (
    "No pudimos registrar la solicitud en este momento. Intentá nuevamente en unos minutos."
)


class ProteccionSolicitudPublicaError(Exception):
    mensaje = MENSAJE_PROTECCION_NO_DISPONIBLE
    status_code = 400

    def __init__(self, mensaje=None, *, retry_after=None):
        super().__init__(mensaje or self.mensaje)
        self.mensaje = mensaje or self.mensaje
        self.retry_after = retry_after


class SolicitudPublicaLimitadaError(ProteccionSolicitudPublicaError):
    mensaje = MENSAJE_LIMITE_SOLICITUD_PUBLICA
    status_code = 429


class TurnstileSolicitudPublicaInvalido(ProteccionSolicitudPublicaError):
    mensaje = MENSAJE_TURNSTILE_SOLICITUD_PUBLICA
    status_code = 400


class ProteccionSolicitudPublicaNoDisponible(ProteccionSolicitudPublicaError):
    mensaje = MENSAJE_PROTECCION_NO_DISPONIBLE
    status_code = 503


class IdempotenciaSolicitudPublicaInvalida(ProteccionSolicitudPublicaError):
    mensaje = MENSAJE_PROTECCION_NO_DISPONIBLE
    status_code = 400


@dataclass(frozen=True)
class IntentoSolicitudPublica:
    ip_hash: str
    dni_hash: str
    documento: str
    requiere_turnstile: bool


@dataclass(frozen=True)
class ResultadoIdempotencia:
    estado: str
    token_hash: str

    @property
    def debe_procesar(self):
        return self.estado == "processing"

    @property
    def es_repetido(self):
        return self.estado in {"processing_existing", "completed"}


def generar_idempotency_token(request):
    tokens = _obtener_tokens_de_session(request)
    token = token_urlsafe(32)
    tokens[token] = int(timezone.now().timestamp())
    request.session[SESSION_IDEMPOTENCY_KEY] = tokens
    request.session.modified = True
    return token


def turnstile_requerido_para_request(request, documento=None):
    if not settings.TURNSTILE_ENABLED:
        return False

    documento = normalizar_documento(documento or request.POST.get("documento"))
    ip_hash = _hash_ip(request)

    try:
        intentos_ip = leer_contador(RATE_LIMIT_IP_NAME, ip_hash)
        intentos_dni = leer_contador(RATE_LIMIT_DNI_NAME, _hash_dni(documento)) if documento else 0
    except Exception as error:
        _registrar_fallo_cache("turnstile_read", error, ip_hash=ip_hash)
        return False

    umbral = settings.TURNOS_PUBLIC_BOOKING_TURNSTILE_AFTER_ATTEMPTS
    return intentos_ip >= umbral or intentos_dni >= umbral


def registrar_intento_creacion_publica(request):
    documento = normalizar_documento(request.POST.get("documento")) or ""
    ip_hash = _hash_ip(request)
    dni_hash = _hash_dni(documento) if documento else ""
    requiere_turnstile = turnstile_requerido_para_request(request, documento)

    if requiere_turnstile:
        resultado_turnstile = validar_turnstile(
            request.POST.get("cf-turnstile-response") or request.POST.get("turnstile_token"),
            obtener_ip_cliente(request),
        )

        if not resultado_turnstile.valido:
            _incrementar_contadores(ip_hash, dni_hash)
            logger.warning(
                "Turnstile invalido en solicitud publica. "
                "reason=turnstile_invalid ip_hash=%s dni_hash=%s error=%s",
                ip_hash,
                dni_hash,
                resultado_turnstile.error,
            )
            raise TurnstileSolicitudPublicaInvalido()

    limite_ip, limite_dni = _incrementar_contadores(ip_hash, dni_hash)
    retry_after = None

    if limite_ip and not limite_ip.permitido:
        retry_after = max(retry_after or 0, settings.TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS)
        logger.warning(
            "Rate limit de solicitud publica alcanzado. "
            "reason=rate_limit_ip ip_hash=%s dni_hash=%s",
            ip_hash,
            dni_hash,
        )

    if limite_dni and not limite_dni.permitido:
        retry_after = max(retry_after or 0, settings.TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS)
        logger.warning(
            "Rate limit de solicitud publica alcanzado. "
            "reason=rate_limit_dni ip_hash=%s dni_hash=%s",
            ip_hash,
            dni_hash,
        )

    if retry_after:
        raise SolicitudPublicaLimitadaError(retry_after=retry_after)

    return IntentoSolicitudPublica(
        ip_hash=ip_hash,
        dni_hash=dni_hash,
        documento=documento,
        requiere_turnstile=requiere_turnstile,
    )


def adquirir_idempotencia(request, token):
    token = (token or "").strip()
    tokens = _obtener_tokens_de_session(request)

    if not token or token not in tokens:
        logger.warning("Token de idempotencia invalido. reason=idempotency_invalid")
        raise IdempotenciaSolicitudPublicaInvalida()

    creado_en = int(tokens[token])
    ahora = int(timezone.now().timestamp())

    if ahora - creado_en > settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS:
        tokens.pop(token, None)
        request.session[SESSION_IDEMPOTENCY_KEY] = tokens
        request.session.modified = True
        logger.warning("Token de idempotencia vencido. reason=idempotency_expired")
        raise IdempotenciaSolicitudPublicaInvalida()

    token_hash = hash_valor_publico(token, "booking_idempotency")
    cache_key = _idempotency_cache_key(token_hash)

    try:
        reservado = cache.add(
            cache_key,
            "processing",
            timeout=settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS,
        )

        if reservado:
            return ResultadoIdempotencia("processing", token_hash)

        estado = cache.get(cache_key)
    except Exception as error:
        _registrar_fallo_cache("idempotency", error)
        raise ProteccionSolicitudPublicaNoDisponible() from error

    if estado == "completed":
        return ResultadoIdempotencia("completed", token_hash)

    logger.warning(
        "Token de idempotencia repetido. reason=idempotency_repeated token_hash=%s",
        token_hash,
    )
    return ResultadoIdempotencia("processing_existing", token_hash)


def completar_idempotencia(token_hash):
    try:
        cache.set(
            _idempotency_cache_key(token_hash),
            "completed",
            timeout=settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS,
        )
    except Exception as error:
        _registrar_fallo_cache("idempotency_complete", error)
        raise ProteccionSolicitudPublicaNoDisponible() from error


def liberar_idempotencia(token_hash):
    try:
        cache.delete(_idempotency_cache_key(token_hash))
    except Exception as error:
        _registrar_fallo_cache("idempotency_release", error)
        raise ProteccionSolicitudPublicaNoDisponible() from error


def _incrementar_contadores(ip_hash, dni_hash):
    try:
        limite_ip = incrementar_limite(
            RATE_LIMIT_IP_NAME,
            ip_hash,
            settings.TURNOS_PUBLIC_BOOKING_IP_LIMIT,
            settings.TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS,
        )
        limite_dni = (
            incrementar_limite(
                RATE_LIMIT_DNI_NAME,
                dni_hash,
                settings.TURNOS_PUBLIC_BOOKING_DNI_LIMIT,
                settings.TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS,
            )
            if dni_hash
            else None
        )
    except Exception as error:
        _registrar_fallo_cache("rate_limit", error, ip_hash=ip_hash, dni_hash=dni_hash)
        raise ProteccionSolicitudPublicaNoDisponible() from error

    return limite_ip, limite_dni


def _obtener_tokens_de_session(request):
    tokens = dict(request.session.get(SESSION_IDEMPOTENCY_KEY, {}) or {})
    ahora = int(timezone.now().timestamp())
    vigencia = settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS
    tokens = {
        token: creado_en
        for token, creado_en in tokens.items()
        if ahora - int(creado_en) <= vigencia
    }
    return tokens


def _hash_ip(request):
    return hash_valor_publico(obtener_ip_cliente(request) or "unknown", "booking_ip")


def _hash_dni(documento):
    return hash_valor_publico(documento or "sin-documento", "booking_dni")


def _idempotency_cache_key(token_hash):
    return f"turnos:public_booking:{IDEMPOTENCY_CACHE_NAME}:{token_hash}"


def _registrar_fallo_cache(reason, error, *, ip_hash="", dni_hash=""):
    logger.warning(
        "Proteccion de solicitud publica no disponible. reason=%s error=%s ip_hash=%s dni_hash=%s",
        reason,
        error.__class__.__name__,
        ip_hash,
        dni_hash,
    )
