from .integrations.google_calendar import CALENDARIO_PRINCIPAL
from .models import GoogleCalendarConexion


def guardar_tokens_oauth_de_odontologo(odontologo, tokens):
    conexion, _ = GoogleCalendarConexion.objects.get_or_create(
        odontologo=odontologo,
        defaults={"calendar_id": CALENDARIO_PRINCIPAL},
    )
    conexion.activa = True
    conexion.registrar_tokens(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_expira_en=tokens.token_expira_en,
        scopes=tokens.scopes,
        token_type=tokens.token_type,
    )
    conexion.save()
    return conexion


def desconectar_google_calendar_de_odontologo(odontologo):
    conexion = GoogleCalendarConexion.objects.filter(odontologo=odontologo).first()

    if conexion:
        conexion.desconectar()

    return conexion
