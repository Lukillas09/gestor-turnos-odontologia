import base64
import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from config.email_backends import (
    BrevoEmailApiClient,
    EmailApiError,
    ResendEmailApiClient,
)


class EmailApiResponseFake:
    status = 201

    def __init__(self, contenido=b'{"messageId":"mensaje-ficticio"}'):
        self.contenido = contenido

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.contenido


@override_settings(
    DEFAULT_FROM_EMAIL="consultorio@example.test",
    EMAIL_API_ATTACHMENT_MAX_BYTES=1024,
)
class ResendAttachmentTests(SimpleTestCase):
    def setUp(self):
        self.cliente = ResendEmailApiClient(api_key="clave-ficticia", timeout=1)

    def test_mensaje_sin_adjunto_conserva_payload_existente(self):
        mensaje = EmailMessage(
            subject="Asunto ficticio",
            body="Cuerpo ficticio",
            to=["destino@example.test"],
        )

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(payload["text"], "Cuerpo ficticio")
        self.assertNotIn("attachments", payload)

    def test_pdf_se_codifica_en_base64_con_nombre_y_mime(self):
        contenido = b"%PDF-1.7 contenido ficticio"
        mensaje = EmailMessage(
            subject="Indicaciones ficticias",
            body="Adjunto ficticio",
            to=["destino@example.test"],
            headers={"Idempotency-Key": "indicacion-ficticia-1"},
        )
        mensaje.attach("indicaciones-prueba.pdf", contenido, "application/pdf")

        payload = self.cliente.construir_payload(mensaje)
        headers = self.cliente.construir_headers_mensaje(mensaje)

        self.assertEqual(
            payload["attachments"],
            [
                {
                    "content": base64.b64encode(contenido).decode("ascii"),
                    "filename": "indicaciones-prueba.pdf",
                    "content_type": "application/pdf",
                }
            ],
        )
        self.assertEqual(headers, {"Idempotency-Key": "indicacion-ficticia-1"})

    def test_nombre_de_adjunto_descarta_componentes_de_ruta(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.attach("../../privado/documento.pdf", b"%PDF-prueba", "application/pdf")

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(payload["attachments"][0]["filename"], "documento.pdf")

    def test_rechaza_adjunto_demasiado_grande(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.attach("documento.pdf", b"x" * 1025, "application/pdf")

        with self.assertRaisesMessage(EmailApiError, "tamaño máximo"):
            self.cliente.construir_payload(mensaje)

    def test_rechaza_clave_de_idempotencia_con_salto_de_linea(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.extra_headers["Idempotency-Key"] = "valor\r\ninyectado"

        with self.assertRaisesMessage(EmailApiError, "no es válida"):
            self.cliente.construir_headers_mensaje(mensaje)

    def test_error_http_es_controlado_y_no_incluye_api_key(self):
        error = HTTPError(
            url="https://api.resend.com/emails",
            code=503,
            msg="Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"message":"error temporal"}'),
        )

        with (
            patch("config.email_backends.urlopen", side_effect=error),
            self.assertRaises(EmailApiError) as contexto,
        ):
            self.cliente._post_json(
                payload={"to": ["destino@example.test"]},
                headers=self.cliente.construir_headers(),
            )

        self.assertIn("HTTP 503", str(contexto.exception))
        self.assertNotIn("clave-ficticia", str(contexto.exception))
        self.assertNotIn("error temporal", str(contexto.exception))


@override_settings(
    DEFAULT_FROM_EMAIL="Consultorio <consultorio@example.test>",
    EMAIL_API_ATTACHMENT_MAX_BYTES=1024,
)
class BrevoAttachmentTests(SimpleTestCase):
    def setUp(self):
        self.cliente = BrevoEmailApiClient(api_key="clave-brevo-ficticia", timeout=1)

    def test_mensaje_sin_adjunto_conserva_payload_existente(self):
        mensaje = EmailMessage(
            subject="Asunto ficticio",
            body="Cuerpo ficticio",
            to=["destino@example.test"],
        )

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(payload["textContent"], "Cuerpo ficticio")
        self.assertNotIn("attachment", payload)

    def test_pdf_se_codifica_en_base64_con_nombre_exacto(self):
        contenido = b"%PDF-1.7 contenido ficticio"
        mensaje = EmailMessage(
            subject="Indicaciones ficticias",
            body="Adjunto ficticio",
            to=["destino@example.test"],
        )
        mensaje.attach("indicaciones-prueba.pdf", contenido, "application/pdf")

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(
            payload["attachment"],
            [
                {
                    "content": base64.b64encode(contenido).decode("ascii"),
                    "name": "indicaciones-prueba.pdf",
                }
            ],
        )

    def test_html_texto_y_pdf_comparten_el_payload(self):
        mensaje = EmailMultiAlternatives(
            subject="Indicaciones ficticias",
            body="Versión de texto",
            to=["destino@example.test"],
        )
        mensaje.attach_alternative("<p>Versión HTML</p>", "text/html")
        mensaje.attach("documento.pdf", b"%PDF-prueba", "application/pdf")

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(payload["textContent"], "Versión de texto")
        self.assertEqual(payload["htmlContent"], "<p>Versión HTML</p>")
        self.assertEqual(payload["attachment"][0]["name"], "documento.pdf")

    def test_nombre_de_adjunto_descarta_componentes_de_ruta(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.attach("../../privado/documento.pdf", b"%PDF-prueba", "application/pdf")

        payload = self.cliente.construir_payload(mensaje)

        self.assertEqual(payload["attachment"][0]["name"], "documento.pdf")

    def test_rechaza_adjunto_demasiado_grande(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.attach("documento.pdf", b"x" * 1025, "application/pdf")

        with self.assertRaisesMessage(EmailApiError, "tamaño máximo"):
            self.cliente.construir_payload(mensaje)

    def test_envio_publica_attachment_en_brevo(self):
        mensaje = EmailMessage(subject="Prueba", body="Prueba", to=["destino@example.test"])
        mensaje.attach("documento.pdf", b"%PDF-prueba", "application/pdf")

        with patch(
            "config.email_backends.urlopen",
            return_value=EmailApiResponseFake(),
        ) as urlopen_mock:
            self.cliente.enviar(mensaje)

        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["attachment"][0]["name"], "documento.pdf")

    def test_error_http_no_expone_respuesta_ni_clave(self):
        contenido_sensible = "service-role=ficticia&url_firmada=https://example.test/secreto"
        error = HTTPError(
            url="https://api.brevo.com/v3/smtp/email",
            code=503,
            msg="Unavailable",
            hdrs=None,
            fp=BytesIO(contenido_sensible.encode("utf-8")),
        )

        with (
            patch("config.email_backends.urlopen", side_effect=error),
            self.assertRaises(EmailApiError) as contexto,
        ):
            self.cliente._post_json(
                payload={"to": [{"email": "destino@example.test"}]},
                headers=self.cliente.construir_headers(),
            )

        mensaje_error = str(contexto.exception)
        self.assertIn("HTTP 503", mensaje_error)
        self.assertNotIn("clave-brevo-ficticia", mensaje_error)
        self.assertNotIn(contenido_sensible, mensaje_error)

    def test_respuesta_invalida_no_expone_su_contenido(self):
        contenido_sensible = b"token-super-secreto-no-json"

        with (
            patch(
                "config.email_backends.urlopen",
                return_value=EmailApiResponseFake(contenido_sensible),
            ),
            self.assertRaisesMessage(EmailApiError, "respuesta inválida") as contexto,
        ):
            self.cliente._post_json(
                payload={"to": [{"email": "destino@example.test"}]},
                headers=self.cliente.construir_headers(),
            )

        self.assertNotIn(contenido_sensible.decode("ascii"), str(contexto.exception))

    def test_error_de_red_no_expone_el_motivo(self):
        motivo_sensible = "fallo conectado a https://usuario:clave@example.test"

        with (
            patch(
                "config.email_backends.urlopen",
                side_effect=URLError(motivo_sensible),
            ),
            self.assertRaises(EmailApiError) as contexto,
        ):
            self.cliente._post_json(
                payload={"to": [{"email": "destino@example.test"}]},
                headers=self.cliente.construir_headers(),
            )

        self.assertNotIn(motivo_sensible, str(contexto.exception))
