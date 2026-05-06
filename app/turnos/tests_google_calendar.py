from django.test import SimpleTestCase, override_settings

from turnos.integrations.google_calendar import obtener_configuracion_google_calendar


class GoogleCalendarConfigTests(SimpleTestCase):
    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="",
        GOOGLE_CALENDAR_CLIENT_SECRET="",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_no_esta_lista_sin_credenciales(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertFalse(configuracion.esta_configurada)

    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="client-id",
        GOOGLE_CALENDAR_CLIENT_SECRET="client-secret",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_esta_lista_con_cliente_y_secreto(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertTrue(configuracion.esta_configurada)

    @override_settings(
        GOOGLE_CALENDAR_CLIENT_ID="",
        GOOGLE_CALENDAR_CLIENT_SECRET="",
        GOOGLE_CALENDAR_CLIENT_SECRETS_FILE="secrets/google-client-secret.json",
        GOOGLE_CALENDAR_REDIRECT_URI="http://127.0.0.1:8000/google/oauth2/callback/",
        GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar.events"],
    )
    def test_configuracion_esta_lista_con_archivo_de_credenciales(self):
        configuracion = obtener_configuracion_google_calendar()

        self.assertTrue(configuracion.esta_configurada)
