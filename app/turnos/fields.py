import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

ENCRYPTED_TEXT_PREFIX = "enc:v1:"
_fernet = None
_fernet_key = None


def _obtener_clave_fernet():
    clave = getattr(settings, "OAUTH_TOKEN_ENCRYPTION_KEY", "")

    if clave:
        clave_bytes = clave.encode("utf-8") if isinstance(clave, str) else clave

        try:
            Fernet(clave_bytes)
        except (TypeError, ValueError) as error:
            raise ImproperlyConfigured(
                "OAUTH_TOKEN_ENCRYPTION_KEY debe ser una clave Fernet valida."
            ) from error

        return clave_bytes

    if getattr(settings, "OAUTH_TOKEN_ENCRYPTION_KEY_REQUIRED", False):
        raise ImproperlyConfigured(
            "OAUTH_TOKEN_ENCRYPTION_KEY debe configurarse cuando DJANGO_DEBUG=False."
        )

    digest = hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _obtener_fernet():
    global _fernet, _fernet_key

    clave = _obtener_clave_fernet()

    if _fernet is None or _fernet_key != clave:
        _fernet = Fernet(clave)
        _fernet_key = clave

    return _fernet


def cifrar_texto_en_reposo(valor):
    if valor in (None, ""):
        return valor

    valor = str(valor)

    if valor.startswith(ENCRYPTED_TEXT_PREFIX):
        return valor

    token = _obtener_fernet().encrypt(valor.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_TEXT_PREFIX}{token}"


def descifrar_texto_en_reposo(valor):
    if valor in (None, ""):
        return valor

    valor = str(valor)

    if not valor.startswith(ENCRYPTED_TEXT_PREFIX):
        return valor

    token = valor.removeprefix(ENCRYPTED_TEXT_PREFIX).encode("utf-8")

    try:
        return _obtener_fernet().decrypt(token).decode("utf-8")
    except InvalidToken as error:
        raise ImproperlyConfigured(
            "No se pudo descifrar un valor protegido. Revisar OAUTH_TOKEN_ENCRYPTION_KEY."
        ) from error


class EncryptedTextField(models.TextField):
    description = "TextField cifrado en reposo con Fernet"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return cifrar_texto_en_reposo(value)

    def from_db_value(self, value, expression, connection):
        return descifrar_texto_en_reposo(value)

    def to_python(self, value):
        value = super().to_python(value)
        return descifrar_texto_en_reposo(value)
