import json
import logging
from dataclasses import dataclass
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResultadoTurnstile:
    valido: bool
    requerido: bool = False
    error: str = ""


def turnstile_habilitado():
    return bool(settings.TURNSTILE_ENABLED)


def validar_turnstile(token, remoteip=""):
    if not turnstile_habilitado():
        return ResultadoTurnstile(valido=True, requerido=False)

    if not token or not settings.TURNSTILE_SECRET_KEY:
        return ResultadoTurnstile(valido=False, requerido=True, error="faltan_credenciales")

    payload = {
        "secret": settings.TURNSTILE_SECRET_KEY,
        "response": token,
    }

    if remoteip:
        payload["remoteip"] = remoteip

    request = Request(
        settings.TURNSTILE_VERIFY_URL,
        data=urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        # URL fija del endpoint oficial de Cloudflare Turnstile.
        with urlopen(request, timeout=settings.TURNSTILE_TIMEOUT_SECONDS) as response:  # nosec B310
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("No se pudo validar Turnstile para acceso público.")
        return ResultadoTurnstile(valido=False, requerido=True, error=error.__class__.__name__)

    return ResultadoTurnstile(
        valido=bool(data.get("success")),
        requerido=True,
        error=",".join(data.get("error-codes", [])),
    )
