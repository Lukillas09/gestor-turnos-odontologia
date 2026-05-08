import json
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, get_connection
from django.test import SimpleTestCase, override_settings


class EmailApiResponseFake:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"id":"email-test"}'


class EmailApiBackendTests(SimpleTestCase):
    @override_settings(
        EMAIL_BACKEND="config.email_backends.EmailApiBackend",
        EMAIL_API_PROVIDER="resend",
        EMAIL_API_KEY="re_test",
        EMAIL_API_URL="",
        DEFAULT_FROM_EMAIL="Consultorio <turnos@example.com>",
    )
    @patch("config.email_backends.urlopen", return_value=EmailApiResponseFake())
    def test_envia_email_con_resend(self, urlopen_mock):
        enviados = EmailMessage(
            subject="Turno confirmado",
            body="Tu turno fue confirmado.",
            to=["paciente@example.com"],
        ).send()

        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}

        self.assertEqual(enviados, 1)
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(headers["authorization"], "Bearer re_test")
        self.assertEqual(headers["user-agent"], "gestor-turnos-odontologia/1.0")
        self.assertEqual(payload["from"], "Consultorio <turnos@example.com>")
        self.assertEqual(payload["to"], ["paciente@example.com"])
        self.assertEqual(payload["subject"], "Turno confirmado")
        self.assertEqual(payload["text"], "Tu turno fue confirmado.")

    @override_settings(
        EMAIL_BACKEND="config.email_backends.EmailApiBackend",
        EMAIL_API_PROVIDER="brevo",
        EMAIL_API_KEY="brevo-test",
        EMAIL_API_URL="",
        DEFAULT_FROM_EMAIL="Consultorio <turnos@example.com>",
    )
    @patch("config.email_backends.urlopen", return_value=EmailApiResponseFake())
    def test_envia_email_con_brevo(self, urlopen_mock):
        enviados = EmailMessage(
            subject="Turno confirmado",
            body="Tu turno fue confirmado.",
            to=["paciente@example.com"],
        ).send()

        request = urlopen_mock.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        headers = {key.lower(): value for key, value in request.header_items()}

        self.assertEqual(enviados, 1)
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(headers["api-key"], "brevo-test")
        self.assertEqual(headers["user-agent"], "gestor-turnos-odontologia/1.0")
        self.assertEqual(
            payload["sender"],
            {"name": "Consultorio", "email": "turnos@example.com"},
        )
        self.assertEqual(payload["to"], [{"email": "paciente@example.com"}])
        self.assertEqual(payload["subject"], "Turno confirmado")
        self.assertEqual(payload["textContent"], "Tu turno fue confirmado.")

    @override_settings(
        EMAIL_BACKEND="config.email_backends.EmailApiBackend",
        EMAIL_API_PROVIDER="resend",
        EMAIL_API_KEY="",
    )
    def test_falla_si_falta_api_key(self):
        connection = get_connection(fail_silently=False)

        with self.assertRaises(ImproperlyConfigured):
            EmailMessage(
                subject="Turno confirmado",
                body="Tu turno fue confirmado.",
                to=["paciente@example.com"],
                connection=connection,
            ).send()

    @override_settings(
        EMAIL_BACKEND="config.email_backends.EmailApiBackend",
        EMAIL_API_PROVIDER="proveedor-invalido",
        EMAIL_API_KEY="api-key",
    )
    def test_falla_si_el_proveedor_no_esta_soportado(self):
        connection = get_connection(fail_silently=False)

        with self.assertRaises(ImproperlyConfigured):
            EmailMessage(
                subject="Turno confirmado",
                body="Tu turno fue confirmado.",
                to=["paciente@example.com"],
                connection=connection,
            ).send()

    @override_settings(
        EMAIL_BACKEND="config.email_backends.EmailApiBackend",
        EMAIL_API_PROVIDER="resend",
        EMAIL_API_KEY="",
    )
    def test_puede_fallar_en_silencio_si_falta_configuracion(self):
        connection = get_connection(fail_silently=True)

        enviados = EmailMessage(
            subject="Turno confirmado",
            body="Tu turno fue confirmado.",
            to=["paciente@example.com"],
            connection=connection,
        ).send()

        self.assertEqual(enviados, 0)
