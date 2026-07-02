from django.conf import settings
from django.core import signing


PUBLIC_TURNO_TOKEN_SALT = "turnos.public-actions.v1"
DEFAULT_PUBLIC_TURNO_TOKEN_SECONDS = 60 * 60 * 24


def obtener_expiracion_token_accion_publica():
    return int(
        getattr(
            settings,
            "TURNOS_PUBLIC_ACTION_TOKEN_SECONDS",
            DEFAULT_PUBLIC_TURNO_TOKEN_SECONDS,
        )
    )


def crear_token_accion_publica_turno(turno):
    return signing.dumps(
        {"turno_id": turno.pk},
        salt=PUBLIC_TURNO_TOKEN_SALT,
        compress=True,
    )


def obtener_turno_id_desde_token_accion_publica(token):
    data = signing.loads(
        token,
        salt=PUBLIC_TURNO_TOKEN_SALT,
        max_age=obtener_expiracion_token_accion_publica(),
    )
    return int(data["turno_id"])
