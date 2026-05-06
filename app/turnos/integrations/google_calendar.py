from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class GoogleCalendarConfig:
    client_id: str
    client_secret: str
    client_secrets_file: str
    redirect_uri: str
    scopes: list[str]

    @property
    def esta_configurada(self):
        tiene_cliente_env = bool(self.client_id and self.client_secret)
        tiene_archivo_credenciales = bool(self.client_secrets_file)
        return tiene_cliente_env or tiene_archivo_credenciales


def obtener_configuracion_google_calendar():
    return GoogleCalendarConfig(
        client_id=settings.GOOGLE_CALENDAR_CLIENT_ID,
        client_secret=settings.GOOGLE_CALENDAR_CLIENT_SECRET,
        client_secrets_file=settings.GOOGLE_CALENDAR_CLIENT_SECRETS_FILE,
        redirect_uri=settings.GOOGLE_CALENDAR_REDIRECT_URI,
        scopes=settings.GOOGLE_CALENDAR_SCOPES,
    )
