import base64
import json
import re
from email.utils import parseaddr
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.text import get_valid_filename


class EmailApiError(Exception):
    pass


class EmailApiBackend(BaseEmailBackend):
    proveedores = {
        "resend": "resend",
        "brevo": "brevo",
        "sendinblue": "brevo",
    }

    def __init__(
        self,
        provider=None,
        api_key=None,
        api_url=None,
        timeout=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.provider = (provider or settings.EMAIL_API_PROVIDER).strip().lower()
        self.api_key = api_key or settings.EMAIL_API_KEY
        self.api_url = api_url or settings.EMAIL_API_URL
        self.timeout = timeout or settings.EMAIL_TIMEOUT

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        try:
            cliente = self._crear_cliente()
        except Exception:
            if not self.fail_silently:
                raise
            return 0

        enviados = 0

        for email_message in email_messages:
            try:
                cliente.enviar(email_message)
            except Exception:
                if not self.fail_silently:
                    raise
            else:
                enviados += 1

        return enviados

    def _crear_cliente(self):
        proveedor = self.proveedores.get(self.provider)

        if not proveedor:
            raise ImproperlyConfigured("EMAIL_API_PROVIDER debe ser 'resend' o 'brevo'.")

        if not self.api_key:
            raise ImproperlyConfigured("EMAIL_API_KEY debe configurarse para usar EmailApiBackend.")

        if proveedor == "resend":
            return ResendEmailApiClient(
                api_key=self.api_key,
                api_url=self.api_url,
                timeout=self.timeout,
            )

        return BrevoEmailApiClient(
            api_key=self.api_key,
            api_url=self.api_url,
            timeout=self.timeout,
        )


class BaseEmailApiClient:
    default_api_url = ""
    soporta_adjuntos = False

    def __init__(self, api_key, api_url=None, timeout=10):
        self.api_key = api_key
        self.api_url = api_url or self.default_api_url
        self.timeout = timeout

    def enviar(self, email_message):
        if email_message.attachments and not self.soporta_adjuntos:
            raise EmailApiError("El backend de email por API no soporta adjuntos.")

        payload = self.construir_payload(email_message)
        headers = self.construir_headers()
        headers.update(self.construir_headers_mensaje(email_message))
        self._post_json(payload=payload, headers=headers)

    def construir_payload(self, email_message):
        raise NotImplementedError

    def construir_headers(self):
        raise NotImplementedError

    def construir_headers_mensaje(self, email_message):
        return {}

    def _post_json(self, payload, headers):
        request = Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "gestor-turnos-odontologia/1.0",
                **headers,
            },
            method="POST",
        )

        try:
            # URL de proveedor validada por configuración EMAIL_API_URL.
            with urlopen(request, timeout=self.timeout) as response:  # nosec B310
                if response.status >= 400:
                    raise EmailApiError(f"El proveedor de email respondio HTTP {response.status}.")
        except HTTPError as error:
            detalle = error.read().decode("utf-8", errors="replace")[:500]
            raise EmailApiError(
                f"El proveedor de email respondio HTTP {error.code}: {detalle}"
            ) from error
        except URLError as error:
            raise EmailApiError(
                f"No se pudo conectar con el proveedor de email: {error.reason}"
            ) from error


class ResendEmailApiClient(BaseEmailApiClient):
    default_api_url = "https://api.resend.com/emails"
    soporta_adjuntos = True

    def construir_headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
        }

    def construir_headers_mensaje(self, email_message):
        clave = (getattr(email_message, "extra_headers", {}) or {}).get(
            "Idempotency-Key",
            "",
        )
        if not clave:
            return {}
        if len(clave) > 256 or "\r" in clave or "\n" in clave:
            raise EmailApiError("La clave de idempotencia del email no es válida.")
        return {"Idempotency-Key": clave}

    def construir_payload(self, email_message):
        text_content, html_content = obtener_contenido(email_message)
        payload = {
            "from": email_message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": list(email_message.to),
            "subject": email_message.subject,
        }

        if email_message.cc:
            payload["cc"] = list(email_message.cc)

        if email_message.bcc:
            payload["bcc"] = list(email_message.bcc)

        if email_message.reply_to:
            payload["reply_to"] = list(email_message.reply_to)

        if html_content:
            payload["html"] = html_content

        if text_content:
            payload["text"] = text_content

        if email_message.attachments:
            payload["attachments"] = [
                _construir_adjunto_resend(adjunto) for adjunto in email_message.attachments
            ]

        return payload


class BrevoEmailApiClient(BaseEmailApiClient):
    default_api_url = "https://api.brevo.com/v3/smtp/email"

    def construir_headers(self):
        return {
            "api-key": self.api_key,
        }

    def construir_payload(self, email_message):
        text_content, html_content = obtener_contenido(email_message)
        payload = {
            "sender": construir_contacto(email_message.from_email or settings.DEFAULT_FROM_EMAIL),
            "to": [construir_contacto(destinatario) for destinatario in email_message.to],
            "subject": email_message.subject,
        }

        if email_message.cc:
            payload["cc"] = [construir_contacto(destinatario) for destinatario in email_message.cc]

        if email_message.bcc:
            payload["bcc"] = [
                construir_contacto(destinatario) for destinatario in email_message.bcc
            ]

        if email_message.reply_to:
            payload["replyTo"] = construir_contacto(email_message.reply_to[0])

        if html_content:
            payload["htmlContent"] = html_content

        if text_content:
            payload["textContent"] = text_content

        return payload


def obtener_contenido(email_message):
    text_content = ""
    html_content = ""

    if getattr(email_message, "content_subtype", "") == "html":
        html_content = email_message.body
    else:
        text_content = email_message.body

    for alternative in getattr(email_message, "alternatives", []):
        if hasattr(alternative, "content"):
            contenido = alternative.content
            mimetype = alternative.mimetype
        else:
            contenido = alternative[0]
            mimetype = alternative[1]

        if mimetype == "text/html":
            html_content = contenido
        elif mimetype == "text/plain":
            text_content = contenido

    return text_content, html_content


def construir_contacto(address):
    nombre, email = parseaddr(address)
    contacto = {"email": email or address}

    if nombre:
        contacto["name"] = nombre

    return contacto


PATRON_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


def _construir_adjunto_resend(adjunto):
    nombre, contenido, content_type = _extraer_adjunto(adjunto)
    nombre_seguro = _nombre_adjunto_seguro(nombre)
    content_type = (content_type or "application/octet-stream").strip().lower()
    if not PATRON_CONTENT_TYPE.fullmatch(content_type):
        raise EmailApiError("El tipo de contenido del adjunto no es válido.")
    if isinstance(contenido, str):
        contenido = contenido.encode("utf-8")
    if not isinstance(contenido, bytes):
        try:
            contenido = bytes(contenido)
        except (TypeError, ValueError) as error:
            raise EmailApiError("El contenido del adjunto no es binario.") from error
    limite = int(
        getattr(
            settings,
            "EMAIL_API_ATTACHMENT_MAX_BYTES",
            getattr(settings, "INDICACIONES_PDF_MAX_BYTES", 5 * 1024 * 1024),
        )
    )
    if not contenido:
        raise EmailApiError("El adjunto está vacío.")
    if len(contenido) > limite:
        raise EmailApiError("El adjunto supera el tamaño máximo permitido.")
    return {
        "content": base64.b64encode(contenido).decode("ascii"),
        "filename": nombre_seguro,
        "content_type": content_type,
    }


def _extraer_adjunto(adjunto):
    if hasattr(adjunto, "get_payload"):
        return (
            adjunto.get_filename(),
            adjunto.get_payload(decode=True),
            adjunto.get_content_type(),
        )
    try:
        return adjunto.filename, adjunto.content, adjunto.mimetype
    except AttributeError:
        try:
            return adjunto[0], adjunto[1], adjunto[2]
        except (IndexError, TypeError) as error:
            raise EmailApiError("El formato del adjunto no es compatible.") from error


def _nombre_adjunto_seguro(nombre):
    nombre_base = Path(str(nombre or "").replace("\\", "/")).name
    nombre_seguro = get_valid_filename(nombre_base)
    if not nombre_seguro or nombre_seguro in {".", ".."}:
        raise EmailApiError("El nombre del adjunto no es válido.")
    return nombre_seguro[:180]
