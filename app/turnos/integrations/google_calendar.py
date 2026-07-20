import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from turnos.models import Turno

CALENDARIO_PRINCIPAL = "primary"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
HTTP_TIMEOUT_SEGUNDOS = 10


class GoogleCalendarError(RuntimeError):
    """Error base para la integracion con Google Calendar."""


class GoogleCalendarClienteNoConfiguradoError(GoogleCalendarError):
    pass


class GoogleCalendarEventoSinIdError(GoogleCalendarError):
    pass


class GoogleCalendarCredencialesOAuthIncompletasError(GoogleCalendarError):
    pass


class GoogleCalendarHTTPError(GoogleCalendarError):
    pass


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


@dataclass(frozen=True)
class GoogleCalendarEvento:
    resumen: str
    descripcion: str
    inicio: str
    fin: str
    zona_horaria: str
    estado_google: str
    metadata_privada: dict[str, str]

    def como_payload(self):
        payload = {
            "summary": self.resumen,
            "description": self.descripcion,
            "start": {
                "dateTime": self.inicio,
                "timeZone": self.zona_horaria,
            },
            "end": {
                "dateTime": self.fin,
                "timeZone": self.zona_horaria,
            },
            "status": self.estado_google,
        }

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
        payload = construir_evento_desde_turno(turno).como_payload()

        if self.servicio is not None:
            respuesta = self._eventos().insert(calendarId=self.calendar_id, body=payload).execute()
            return respuesta.get("id", "")

        respuesta = self._enviar_request("POST", self._eventos_url(), payload)
        return respuesta.get("id", "")

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
            return (
                self._eventos()
                .delete(
                    calendarId=self.calendar_id,
                    eventId=turno.google_calendar_event_id,
                )
                .execute()
            )

        return self._enviar_request("DELETE", self._evento_url(turno.google_calendar_event_id))

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
    return GoogleCalendarConfig(
        client_id=settings.GOOGLE_CALENDAR_CLIENT_ID,
        client_secret=settings.GOOGLE_CALENDAR_CLIENT_SECRET,
        client_secrets_file=settings.GOOGLE_CALENDAR_CLIENT_SECRETS_FILE,
        redirect_uri=settings.GOOGLE_CALENDAR_REDIRECT_URI,
        scopes=settings.GOOGLE_CALENDAR_SCOPES,
    )


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
    fin = _con_zona_horaria(turno.fecha_hora_fin)

    return GoogleCalendarEvento(
        resumen=f"Turno odontológico - {turno.paciente.nombre_completo}",
        descripcion=_construir_descripcion(turno),
        inicio=inicio.isoformat(),
        fin=fin.isoformat(),
        zona_horaria=settings.TIME_ZONE,
        estado_google=_obtener_estado_google(turno),
        metadata_privada=_construir_metadata_privada(turno),
    )


def _construir_descripcion(turno):
    lineas = [
        f"Paciente: {turno.paciente.nombre_completo}",
        f"Odontólogo: {turno.odontologo.nombre_completo}",
        f"Estado: {turno.get_estado_display()}",
    ]

    if turno.tipo_turno_nombre_snapshot:
        lineas.append(f"Tipo: {turno.tipo_turno_nombre_snapshot}")

    if turno.duracion_atencion_minutos:
        lineas.append(f"Duración aproximada de atención: {turno.duracion_atencion_minutos} minutos")

    if turno.margen_posterior_minutos_snapshot:
        lineas.append(
            f"Margen operativo posterior: {turno.margen_posterior_minutos_snapshot} minutos"
        )

    if turno.motivo and turno.motivo != turno.tipo_turno_nombre_snapshot:
        lineas.append(f"Motivo: {turno.motivo}")

    if turno.notas:
        lineas.append(f"Notas: {turno.notas}")

    return "\n".join(lineas)


def _construir_metadata_privada(turno):
    metadata = {
        "turno_id": turno.pk,
        "paciente_id": turno.paciente_id,
        "odontologo_id": turno.odontologo_id,
        "estado": turno.estado,
    }

    return {
        clave: str(valor) for clave, valor in metadata.items() if valor is not None and valor != ""
    }


def _con_zona_horaria(fecha_hora):
    zona_horaria = ZoneInfo(settings.TIME_ZONE)

    if timezone.is_naive(fecha_hora):
        return timezone.make_aware(fecha_hora, zona_horaria)

    return fecha_hora.astimezone(zona_horaria)


def _obtener_estado_google(turno):
    if turno.estado == Turno.Estado.CANCELADO:
        return "cancelled"

    if turno.estado == Turno.Estado.PENDIENTE:
        return "tentative"

    return "confirmed"


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
        raise GoogleCalendarHTTPError(_obtener_mensaje_http_error(error)) from error
    except URLError as error:
        raise GoogleCalendarHTTPError(
            f"No se pudo conectar con Google Calendar: {error}"
        ) from error

    if not contenido:
        return {}

    return json.loads(contenido)


def _obtener_mensaje_http_error(error):
    try:
        contenido = error.read().decode("utf-8")
        respuesta = json.loads(contenido)
    except (ValueError, json.JSONDecodeError):
        return f"Google Calendar respondio con HTTP {error.code}."

    detalle = respuesta.get("error", {})

    if isinstance(detalle, dict):
        return detalle.get("message") or f"Google Calendar respondio con HTTP {error.code}."

    return str(detalle) or f"Google Calendar respondio con HTTP {error.code}."
