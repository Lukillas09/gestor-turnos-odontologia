from secrets import choice, token_urlsafe
from string import digits

from django.utils.crypto import salted_hmac

from pacientes.normalizacion import normalizar_documento

PUBLIC_ACCESS_SESSION_KEY = "turnos_public_access"
PUBLIC_ACCESS_PENDING_CHALLENGE_KEY = "turnos_public_access_pending_challenge"
PUBLIC_ACTION_TOKENS_SESSION_KEY = "turnos_public_action_tokens"


def hash_valor_publico(valor, proposito):
    return salted_hmac(
        f"turnos.public_access.{proposito}",
        normalizar_documento(valor),
    ).hexdigest()


def obtener_ip_cliente(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR", "unknown")


def generar_codigo_otp():
    return "".join(choice(digits) for _ in range(6))


def generar_token_accion():
    return token_urlsafe(32)
