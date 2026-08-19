import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

CALENDARIO_PRINCIPAL = "primary"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
HTTP_TIMEOUT_SEGUNDOS = 10
GOOGLE_EVENT_ID_NAMESPACE = "gestor-turnos-odontologia:turno:v1:"


class GoogleCalendarError(RuntimeError):
    """Error base para la integracion con Google Calendar."""


class GoogleCalendarClienteNoConfiguradoError(GoogleCalendarError):
    pass


class GoogleCalendarEventoSinIdError(GoogleCalendarError):
    pass


class GoogleCalendarCredencialesOAuthIncompletasError(GoogleCalendarError):
    pass


class GoogleCalendarHTTPError(GoogleCalendarError):
    def __init__(self, mensaje, *, status_code=None):
        super().__init__(mensaje)
        self.status_code = status_code


@dataclass(frozen=True)
class GoogleCalendarConfig:
    client_id: str
    client_secret: str
    client_secrets_file: str
    redirect_uri: str
    scopes: list[str]

    @property
    def esta_configurada(self):
        return bool(self.client_id and self.client_secret)


@dataclass(frozen=True)
class GoogleCalendarEvento:
    event_id: str
    resumen: str
    inicio: str
    fin: str
    zona_horaria: str
    metadata_privada: dict[str, str]

    def como_payload(self, *, incluir_id=False):
        payload = {
            "summary": self.resumen,
            "start": {
                "dateTime": self.inicio,
                "timeZone": self.zona_horaria,
            },
            "end": {
                "dateTime": self.fin,
                "timeZone": self.zona_horaria,
            },
        }

        if incluir_id:
            payload["id"] = self.event_id

        if self.metadata_privada:
            payload["extendedProperties"] = {"private": self.metadata_privada}

        return payload


@dataclass(frozen=True)
class GoogleOAuthTokens:
    access_token: str
    refresh_token: str
    token_type: str
    token_expira_en: datetime | None = None
    scopes: list[str] | None = None


class GoogleCalendarClient:
    def __init__(
        self,
        servicio=None,
        calendar_id=CALENDARIO_PRINCIPAL,
        access_token="",
    ):
        self.servicio = servicio
        self.calendar_id = calendar_id
        self.access_token = access_token

    def crear_evento(self, turno):
        evento = construir_evento_desde_turno(turno)
        payload = evento.como_payload(incluir_id=True)

        if self.servicio is not None:
            try:
                respuesta = (
                    self._eventos().insert(calendarId=self.calendar_id, body=payload).execute()
                )
            except Exception as error:
                if _obtener_status_code(error) != 409:
                    raise
                respuesta = (
                    self._eventos()
                    .update(
                        calendarId=self.calendar_id,
                        eventId=evento.event_id,
                        body=evento.como_payload(),
                    )
                    .execute()
                )
            return respuesta.get("id", evento.event_id)

        try:
            respuesta = self._enviar_request("POST", self._eventos_url(), payload)
        except GoogleCalendarHTTPError as error:
            if error.status_code != 409:
                raise
            respuesta = self._enviar_request(
                "PUT",
                self._evento_url(evento.event_id),
                evento.como_payload(),
            )
        return respuesta.get("id", evento.event_id)

    def actualizar_evento(self, turno):
        if not turno.google_calendar_event_id:
            raise GoogleCalendarEventoSinIdError(
                "No se puede actualizar un evento de Google Calendar sin event_id."
            )

        payload = construir_evento_desde_turno(turno).como_payload()

        if self.servicio is not None:
            respuesta = (
                self._eventos()
                .update(
                    calendarId=self.calendar_id,
                    eventId=turno.google_calendar_event_id,
                    body=payload,
                )
                .execute()
            )
            return respuesta.get("id", turno.google_calendar_event_id)

        respuesta = self._enviar_request(
            "PUT",
            self._evento_url(turno.google_calendar_event_id),
            payload,
        )
        return respuesta.get("id", turno.google_calendar_event_id)

    def cancelar_evento(self, turno):
        if not turno.google_calendar_event_id:
            return None

        if self.servicio is not None:
            try:
                return (
                    self._eventos()
                    .delete(
                        calendarId=self.calendar_id,
                        eventId=turno.google_calendar_event_id,
                    )
                    .execute()
                )
            except Exception as error:
                if _obtener_status_code(error) not in {404, 410}:
                    raise
                return None

        try:
            return self._enviar_request(
                "DELETE",
                self._evento_url(turno.google_calendar_event_id),
            )
        except GoogleCalendarHTTPError as error:
            if error.status_code not in {404, 410}:
                raise
            return None

    def _eventos(self):
        if self.servicio is None:
            raise GoogleCalendarClienteNoConfiguradoError(
                "Todavía no hay un servicio autenticado de Google Calendar. "
                "Primero hay que implementar OAuth y guardar el token del odontólogo."
            )

        return self.servicio.events()

    def _enviar_request(self, metodo, url, payload=None):
        if not self.access_token:
            raise GoogleCalendarClienteNoConfiguradoError(
                "No hay access token disponible para llamar a Google Calendar."
            )

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        data = None

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=data, headers=headers, method=metodo)
        return _ejecutar_request_json(request)

    def _eventos_url(self):
        calendar_id = quote(self.calendar_id, safe="")
        return f"{GOOGLE_CALENDAR_API_BASE_URL}/calendars/{calendar_id}/events"

    def _evento_url(self, event_id):
        event_id = quote(event_id, safe="")
        return f"{self._eventos_url()}/{event_id}"


def obtener_configuracion_google_calendar():
    client_id, client_secret, archivo = _resolver_credenciales_oauth()
    return GoogleCalendarConfig(
        client_id=client_id,
        client_secret=client_secret,
        client_secrets_file=archivo,
        redirect_uri=settings.GOOGLE_CALENDAR_REDIRECT_URI,
        scopes=settings.GOOGLE_CALENDAR_SCOPES,
    )


def _resolver_credenciales_oauth():
    client_id = str(getattr(settings, "GOOGLE_CALENDAR_CLIENT_ID", "") or "").strip()
    client_secret = str(getattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRET", "") or "").strip()

    if client_id and client_secret:
        return client_id, client_secret, ""

    archivo = str(
        getattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRET_FILE", "")
        or getattr(settings, "GOOGLE_CALENDAR_CLIENT_SECRETS_FILE", "")
        or ""
    ).strip()
    if not archivo:
        return "", "", ""

    try:
        contenido = Path(archivo).read_text(encoding="utf-8")
    except OSError as error:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "No se pudo leer el archivo de credenciales OAuth configurado."
        ) from error

    try:
        credenciales = json.loads(contenido)
    except json.JSONDecodeError as error:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "El archivo de credenciales OAuth no contiene JSON válido."
        ) from error

    if not isinstance(credenciales, dict) or not isinstance(credenciales.get("web"), dict):
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "El archivo de credenciales OAuth debe contener un objeto web."
        )

    credenciales_web = credenciales["web"]
    client_id = str(credenciales_web.get("client_id") or "").strip()
    client_secret = str(credenciales_web.get("client_secret") or "").strip()
    if not client_id:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "El archivo de credenciales OAuth no contiene client_id."
        )
    if not client_secret:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "El archivo de credenciales OAuth no contiene client_secret."
        )

    return client_id, client_secret, archivo


def construir_url_autorizacion_google_calendar(state, login_hint=""):
    configuracion = obtener_configuracion_google_calendar()

    if not configuracion.client_id:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "Falta GOOGLE_CALENDAR_CLIENT_ID para iniciar OAuth."
        )

    parametros = {
        "client_id": configuracion.client_id,
        "redirect_uri": configuracion.redirect_uri,
        "response_type": "code",
        "scope": " ".join(configuracion.scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    if login_hint:
        parametros["login_hint"] = login_hint

    return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(parametros)}"


def intercambiar_codigo_por_tokens(code):
    configuracion = obtener_configuracion_google_calendar()

    if not configuracion.client_id or not configuracion.client_secret:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "Faltan GOOGLE_CALENDAR_CLIENT_ID o GOOGLE_CALENDAR_CLIENT_SECRET."
        )

    payload = urlencode(
        {
            "client_id": configuracion.client_id,
            "client_secret": configuracion.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": configuracion.redirect_uri,
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    respuesta = _ejecutar_request_json(request)
    return _construir_tokens_desde_respuesta(respuesta)


def crear_cliente_desde_conexion(conexion):
    if conexion.necesita_renovar_access_token:
        renovar_access_token(conexion)

    return GoogleCalendarClient(
        calendar_id=conexion.calendar_id,
        access_token=conexion.access_token,
    )


def renovar_access_token(conexion):
    configuracion = obtener_configuracion_google_calendar()

    if not configuracion.client_id or not configuracion.client_secret:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "Faltan GOOGLE_CALENDAR_CLIENT_ID o GOOGLE_CALENDAR_CLIENT_SECRET."
        )

    if not conexion.refresh_token:
        raise GoogleCalendarCredencialesOAuthIncompletasError(
            "La conexión no tiene refresh token para renovar el access token."
        )

    payload = urlencode(
        {
            "client_id": configuracion.client_id,
            "client_secret": configuracion.client_secret,
            "refresh_token": conexion.refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_OAUTH_TOKEN_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    respuesta = _ejecutar_request_json(request)
    tokens = _construir_tokens_desde_respuesta(respuesta)

    conexion.registrar_tokens(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_expira_en=tokens.token_expira_en,
        scopes=tokens.scopes,
        token_type=tokens.token_type,
    )
    conexion.save()
    return conexion


def construir_evento_desde_turno(turno):
    inicio = _con_zona_horaria(turno.fecha_hora_inicio)
    fin = _con_zona_horaria(turno.fecha_hora_fin_bloqueada)
    event_id = construir_google_calendar_event_id(turno)

    return GoogleCalendarEvento(
        event_id=event_id,
        resumen="Turno odontológico",
        inicio=inicio.isoformat(),
        fin=fin.isoformat(),
        zona_horaria=settings.TIME_ZONE,
        metadata_privada={
            "source": "gestor-turnos",
            "appointment_ref": event_id,
        },
    )


def construir_google_calendar_event_id(turno):
    if turno.google_calendar_event_id:
        return turno.google_calendar_event_id

    if turno.pk is None:
        raise GoogleCalendarEventoSinIdError(
            "No se puede generar el event ID para un turno sin persistir."
        )

    referencia = f"{GOOGLE_EVENT_ID_NAMESPACE}{turno.pk}".encode()
    return f"gt{sha256(referencia).hexdigest()}"


def _con_zona_horaria(fecha_hora):
    zona_horaria = ZoneInfo(settings.TIME_ZONE)

    if timezone.is_naive(fecha_hora):
        return timezone.make_aware(fecha_hora, zona_horaria)

    return fecha_hora.astimezone(zona_horaria)


def _construir_tokens_desde_respuesta(respuesta):
    access_token = respuesta.get("access_token")

    if not access_token:
        raise GoogleCalendarHTTPError("Google no devolvio un access token en la respuesta OAuth.")

    expires_in = respuesta.get("expires_in")
    token_expira_en = None

    if expires_in:
        token_expira_en = timezone.now() + timedelta(seconds=int(expires_in))

    scopes = None
    scopes_respuesta = respuesta.get("scope")

    if scopes_respuesta:
        scopes = scopes_respuesta.split()

    return GoogleOAuthTokens(
        access_token=access_token,
        refresh_token=respuesta.get("refresh_token", ""),
        token_type=respuesta.get("token_type", "Bearer"),
        token_expira_en=token_expira_en,
        scopes=scopes,
    )


def _ejecutar_request_json(request):
    try:
        # URL fija del endpoint oficial de Google Calendar/OAuth.
        with urlopen(request, timeout=HTTP_TIMEOUT_SEGUNDOS) as response:  # nosec B310
            contenido = response.read().decode("utf-8")
    except HTTPError as error:
        raise GoogleCalendarHTTPError(
            f"Google Calendar respondió con HTTP {error.code}.",
            status_code=error.code,
        ) from error
    except (TimeoutError, URLError) as error:
        raise GoogleCalendarHTTPError("No se pudo conectar con Google Calendar.") from error

    if not contenido:
        return {}

    try:
        respuesta = json.loads(contenido)
    except json.JSONDecodeError as error:
        raise GoogleCalendarHTTPError("Google Calendar devolvió una respuesta inválida.") from error

    if not isinstance(respuesta, dict):
        raise GoogleCalendarHTTPError("Google Calendar devolvió una respuesta inválida.")

    return respuesta


def _obtener_status_code(error):
    if isinstance(error, GoogleCalendarHTTPError):
        return error.status_code

    respuesta = getattr(error, "resp", None)
    return getattr(respuesta, "status", None) or getattr(error, "status_code", None)
