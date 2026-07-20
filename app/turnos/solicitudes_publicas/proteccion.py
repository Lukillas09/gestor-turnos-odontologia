import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from pacientes.normalizacion import normalizar_documento
from turnos.integrations.turnstile import validar_turnstile
from turnos.models import IdempotenciaSolicitudPublica
from turnos.public_access.exceptions import (
    RETRY_AFTER_PROTECCION_PUBLICA_SECONDS,
    ProteccionPublicaNoDisponible,
)
from turnos.public_access.rate_limit import incrementar_limite, leer_contador
from turnos.public_access.tokens import hash_valor_publico, obtener_ip_cliente

logger = logging.getLogger(__name__)

SESSION_IDEMPOTENCY_KEY = "turnos_public_booking_idempotency_tokens"

RATE_LIMIT_IP_NAME = "reserva_creacion_ip"
RATE_LIMIT_DNI_NAME = "reserva_creacion_dni"

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


class ProteccionSolicitudPublicaNoDisponible(
    ProteccionSolicitudPublicaError,
    ProteccionPublicaNoDisponible,
):
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
        return self.estado == IdempotenciaSolicitudPublica.Estado.PROCESSING

    @property
    def es_repetido(self):
        return self.estado in {"processing_existing", IdempotenciaSolicitudPublica.Estado.COMPLETED}


def generar_idempotency_token(request):
    tokens = _obtener_tokens_de_session(request)
    token = token_urlsafe(32)
    token_hash = hash_valor_publico(token, "booking_idempotency")
    tokens[token_hash] = int(timezone.now().timestamp())
    _guardar_tokens_en_session(request, tokens)
    return token


def turnstile_requerido_para_request(request, documento=None):
    if not settings.TURNSTILE_ENABLED:
        return False

    umbral = settings.TURNOS_PUBLIC_BOOKING_TURNSTILE_AFTER_ATTEMPTS

    if umbral <= 0:
        return True

    documento = normalizar_documento(documento or request.POST.get("documento"))
    ip_hash = _hash_ip(request)

    try:
        intentos_ip = (
            leer_contador(
                RATE_LIMIT_IP_NAME,
                ip_hash,
                settings.TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS,
            )
            if settings.TURNOS_PUBLIC_BOOKING_IP_LIMIT > 0
            else 0
        )
        intentos_dni = (
            leer_contador(
                RATE_LIMIT_DNI_NAME,
                _hash_dni(documento),
                settings.TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS,
            )
            if documento and settings.TURNOS_PUBLIC_BOOKING_DNI_LIMIT > 0
            else 0
        )
    except ProteccionPublicaNoDisponible as error:
        _registrar_fallo_db("turnstile_read", error)
        raise ProteccionSolicitudPublicaNoDisponible(
            retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
        ) from error

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
                "Turnstile inválido en solicitud pública. "
                "reason=turnstile_invalid error_type=%s",
                resultado_turnstile.error,
            )
            raise TurnstileSolicitudPublicaInvalido()

    limite_ip, limite_dni = _incrementar_contadores(ip_hash, dni_hash)
    retry_after = None

    if limite_ip and not limite_ip.permitido:
        retry_after = max(retry_after or 0, settings.TURNOS_PUBLIC_BOOKING_IP_WINDOW_SECONDS)
        logger.warning(
            "Rate limit de solicitud pública alcanzado. reason=rate_limit_ip",
        )

    if limite_dni and not limite_dni.permitido:
        retry_after = max(retry_after or 0, settings.TURNOS_PUBLIC_BOOKING_DNI_WINDOW_SECONDS)
        logger.warning(
            "Rate limit de solicitud pública alcanzado. reason=rate_limit_dni",
        )

    if retry_after:
        raise SolicitudPublicaLimitadaError(retry_after=retry_after)

    return IntentoSolicitudPublica(
        ip_hash=ip_hash,
        dni_hash=dni_hash,
        documento=documento,
        requiere_turnstile=requiere_turnstile,
    )


def adquirir_idempotencia(request, token, ahora=None):
    token = (token or "").strip()
    token_hash = hash_valor_publico(token, "booking_idempotency") if token else ""
    tokens = _obtener_tokens_de_session(request)
    session_key = _obtener_clave_token_session(tokens, token, token_hash)

    if not session_key:
        logger.warning("Token de idempotencia inválido. reason=idempotency_invalid")
        raise IdempotenciaSolicitudPublicaInvalida()

    try:
        creado_en_epoch = int(tokens[session_key])
    except (TypeError, ValueError):
        tokens.pop(session_key, None)
        _guardar_tokens_en_session(request, tokens)
        logger.warning("Token de idempotencia inválido. reason=idempotency_invalid_timestamp")
        raise IdempotenciaSolicitudPublicaInvalida() from None

    momento = _normalizar_ahora(ahora)
    token_expira_en = datetime.fromtimestamp(creado_en_epoch, tz=UTC) + timedelta(
        seconds=settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS
    )

    if momento >= token_expira_en:
        tokens.pop(session_key, None)
        _guardar_tokens_en_session(request, tokens)
        logger.warning("Token de idempotencia vencido. reason=idempotency_expired")
        raise IdempotenciaSolicitudPublicaInvalida()

    if session_key != token_hash:
        tokens.pop(session_key, None)
        tokens[token_hash] = creado_en_epoch
        _guardar_tokens_en_session(request, tokens)

    try:
        return _adquirir_idempotencia_transaccional(
            token_hash=token_hash,
            token_expira_en=token_expira_en,
            ahora=momento,
        )
    except DatabaseError as error:
        _registrar_fallo_db("idempotency_acquire", error)
        raise ProteccionSolicitudPublicaNoDisponible(
            retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
        ) from error


def completar_idempotencia(token_hash):
    try:
        with transaction.atomic():
            idempotencia = (
                IdempotenciaSolicitudPublica.objects.select_for_update()
                .filter(token_hash=token_hash)
                .first()
            )

            if idempotencia is None:
                logger.warning(
                    "Idempotencia no disponible al completar. reason=idempotency_missing"
                )
                raise ProteccionSolicitudPublicaNoDisponible(
                    retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
                )

            if idempotencia.estado == IdempotenciaSolicitudPublica.Estado.COMPLETED:
                return

            idempotencia.estado = IdempotenciaSolicitudPublica.Estado.COMPLETED
            idempotencia.procesamiento_expira_en = None
            idempotencia.save(
                update_fields=[
                    "estado",
                    "procesamiento_expira_en",
                    "actualizado_en",
                ]
            )
    except DatabaseError as error:
        _registrar_fallo_db("idempotency_complete", error)
        raise ProteccionSolicitudPublicaNoDisponible(
            retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
        ) from error


def liberar_idempotencia(token_hash):
    try:
        with transaction.atomic():
            idempotencia = (
                IdempotenciaSolicitudPublica.objects.select_for_update()
                .filter(token_hash=token_hash)
                .first()
            )

            if (
                idempotencia is not None
                and idempotencia.estado == IdempotenciaSolicitudPublica.Estado.PROCESSING
            ):
                idempotencia.delete()
    except DatabaseError as error:
        _registrar_fallo_db("idempotency_release", error)
        raise ProteccionSolicitudPublicaNoDisponible(
            retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
        ) from error


def _adquirir_idempotencia_transaccional(*, token_hash, token_expira_en, ahora):
    procesamiento_expira_en = min(
        ahora + timedelta(seconds=settings.TURNOS_PUBLIC_BOOKING_PROCESSING_SECONDS),
        token_expira_en,
    )

    with transaction.atomic():
        idempotencia = (
            IdempotenciaSolicitudPublica.objects.select_for_update()
            .filter(token_hash=token_hash)
            .first()
        )

        if idempotencia is None:
            try:
                with transaction.atomic():
                    IdempotenciaSolicitudPublica.objects.create(
                        token_hash=token_hash,
                        estado=IdempotenciaSolicitudPublica.Estado.PROCESSING,
                        procesamiento_expira_en=procesamiento_expira_en,
                        expira_en=token_expira_en,
                    )
                return ResultadoIdempotencia(
                    IdempotenciaSolicitudPublica.Estado.PROCESSING,
                    token_hash,
                )
            except IntegrityError:
                idempotencia = IdempotenciaSolicitudPublica.objects.select_for_update().get(
                    token_hash=token_hash
                )

        if idempotencia.expira_en <= ahora:
            return _reclamar_idempotencia(
                idempotencia,
                token_expira_en=token_expira_en,
                procesamiento_expira_en=procesamiento_expira_en,
            )

        if idempotencia.estado == IdempotenciaSolicitudPublica.Estado.COMPLETED:
            return ResultadoIdempotencia(
                IdempotenciaSolicitudPublica.Estado.COMPLETED,
                token_hash,
            )

        if idempotencia.estado != IdempotenciaSolicitudPublica.Estado.PROCESSING:
            logger.warning("Estado de idempotencia inválido. reason=idempotency_invalid_state")
            raise ProteccionSolicitudPublicaNoDisponible(
                retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
            )

        if (
            idempotencia.procesamiento_expira_en is not None
            and idempotencia.procesamiento_expira_en > ahora
        ):
            logger.warning("Token de idempotencia repetido. reason=idempotency_repeated")
            return ResultadoIdempotencia("processing_existing", token_hash)

        return _reclamar_idempotencia(
            idempotencia,
            token_expira_en=token_expira_en,
            procesamiento_expira_en=procesamiento_expira_en,
        )


def _reclamar_idempotencia(idempotencia, *, token_expira_en, procesamiento_expira_en):
    idempotencia.estado = IdempotenciaSolicitudPublica.Estado.PROCESSING
    idempotencia.procesamiento_expira_en = procesamiento_expira_en
    idempotencia.expira_en = token_expira_en
    idempotencia.save(
        update_fields=[
            "estado",
            "procesamiento_expira_en",
            "expira_en",
            "actualizado_en",
        ]
    )
    return ResultadoIdempotencia(
        IdempotenciaSolicitudPublica.Estado.PROCESSING,
        idempotencia.token_hash,
    )


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
    except ProteccionPublicaNoDisponible as error:
        _registrar_fallo_db("rate_limit", error)
        raise ProteccionSolicitudPublicaNoDisponible(
            retry_after=RETRY_AFTER_PROTECCION_PUBLICA_SECONDS
        ) from error

    return limite_ip, limite_dni


def _obtener_tokens_de_session(request):
    tokens_guardados = dict(request.session.get(SESSION_IDEMPOTENCY_KEY, {}) or {})
    ahora = int(timezone.now().timestamp())
    vigencia = settings.TURNOS_PUBLIC_BOOKING_IDEMPOTENCY_SECONDS
    tokens_vigentes = {}

    for clave, creado_en in tokens_guardados.items():
        try:
            creado_en = int(creado_en)
        except (TypeError, ValueError):
            continue

        if 0 <= ahora - creado_en <= vigencia:
            tokens_vigentes[clave] = creado_en

    return tokens_vigentes


def _guardar_tokens_en_session(request, tokens):
    request.session[SESSION_IDEMPOTENCY_KEY] = tokens
    request.session.modified = True


def _obtener_clave_token_session(tokens, token, token_hash):
    if not token:
        return ""

    if token_hash in tokens:
        return token_hash

    # Compatibilidad temporal con formularios generados antes de guardar hashes en sesión.
    if token in tokens:
        return token

    return ""


def _normalizar_ahora(ahora):
    momento = ahora or timezone.now()

    if timezone.is_naive(momento):
        raise ValueError("El instante debe incluir zona horaria.")

    return momento.astimezone(UTC)


def _hash_ip(request):
    return hash_valor_publico(obtener_ip_cliente(request) or "unknown", "booking_ip")


def _hash_dni(documento):
    return hash_valor_publico(documento or "sin-documento", "booking_dni")


def _registrar_fallo_db(reason, error):
    logger.warning(
        "Protección de solicitud pública no disponible. reason=%s error_type=%s",
        reason,
        error.__class__.__name__,
    )
