import base64
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.mail import EmailMessage
from django.test import SimpleTestCase, override_settings

from config.email_backends import EmailApiError, ResendEmailApiClient


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
