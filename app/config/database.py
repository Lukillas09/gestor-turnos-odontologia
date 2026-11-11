import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from .env import env_nonnegative_int

POSTGRES_SCHEMES = {"postgres", "postgresql"}
SQLITE_SCHEMES = {"sqlite", "sqlite3"}

# Supavisor en modo transacción. En modo sesión (5432) la conexión se comporta como una directa.
PUERTO_POOLER_MODO_TRANSACCION = 6543


@dataclass(frozen=True)
class AjustesConexionPostgres:
    """Ajustes de conexión de la aplicación a PostgreSQL (en segundos; 0 desactiva el límite).

    Los timeouts de sesión se envían como parámetros de arranque (`OPTIONS["options"]`), por lo
    que solo afectan a la aplicación: `pg_dump` y los scripts de backup usan la URL directa.
    """

    conn_max_age: int = 60
    connect_timeout: int = 5
    lock_timeout: int = 5
    statement_timeout: int = 20
    idle_in_transaction_timeout: int = 60


def cargar_ajustes_postgres_desde_env():
    return AjustesConexionPostgres(
        conn_max_age=env_nonnegative_int("DATABASE_CONN_MAX_AGE", 60),
        connect_timeout=env_nonnegative_int("DATABASE_CONNECT_TIMEOUT_SECONDS", 5),
        lock_timeout=env_nonnegative_int("DATABASE_LOCK_TIMEOUT_SECONDS", 5),
        statement_timeout=env_nonnegative_int("DATABASE_STATEMENT_TIMEOUT_SECONDS", 20),
        idle_in_transaction_timeout=env_nonnegative_int(
            "DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS", 60
        ),
    )


def validar_coherencia_con_gunicorn(ajustes, gunicorn_timeout):
    """La base debe cortar una consulta o una espera antes de que gunicorn mate al worker.

    Sin statement_timeout (0, por ejemplo al migrar) o sin timeout de gunicorn no hay nada que
    comparar.
    """
    if not ajustes.statement_timeout or not gunicorn_timeout:
        return

    if ajustes.statement_timeout + ajustes.lock_timeout >= gunicorn_timeout:
        raise RuntimeError(
            "DATABASE_STATEMENT_TIMEOUT_SECONDS + DATABASE_LOCK_TIMEOUT_SECONDS debe ser menor "
            "que GUNICORN_TIMEOUT para que la base falle antes de que gunicorn mate al worker."
        )


def configurar_base_de_datos(database_url, base_dir, ajustes=None):
    if not database_url:
        return configurar_sqlite_por_defecto(base_dir)

    url = urlparse(database_url)
    esquema = url.scheme.lower()

    if esquema in SQLITE_SCHEMES:
        return configurar_sqlite_desde_url(url, base_dir)

    if esquema in POSTGRES_SCHEMES:
        return configurar_postgres_desde_url(url, ajustes or AjustesConexionPostgres())

    raise RuntimeError(f"El esquema de DATABASE_URL no esta soportado: {url.scheme}.")


def configurar_sqlite_por_defecto(base_dir):
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": base_dir / "db.sqlite3",
    }


def configurar_sqlite_desde_url(url, base_dir):
    nombre = unquote(url.path or "").lstrip("/")

    if not nombre:
        nombre = "db.sqlite3"

    if nombre == ":memory:":
        ruta_base = nombre
    else:
        ruta_base = base_dir / nombre

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ruta_base,
    }


def configurar_postgres_desde_url(url, ajustes):
    nombre_base = unquote(url.path.lstrip("/"))

    if not nombre_base:
        raise RuntimeError("DATABASE_URL debe incluir el nombre de la base PostgreSQL.")

    config: dict[str, Any] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": nombre_base,
        "CONN_MAX_AGE": ajustes.conn_max_age,
        "CONN_HEALTH_CHECKS": True,
    }

    if url.username:
        config["USER"] = unquote(url.username)

    if url.password:
        config["PASSWORD"] = unquote(url.password)

    if url.hostname:
        config["HOST"] = url.hostname

    if url.port:
        config["PORT"] = str(url.port)

    # Lo que ya viene en la URL tiene prioridad sobre los valores por defecto.
    opciones: dict[str, Any] = dict(parse_qsl(url.query, keep_blank_values=False))

    if ajustes.connect_timeout:
        opciones.setdefault("connect_timeout", ajustes.connect_timeout)

    if url.port == PUERTO_POOLER_MODO_TRANSACCION:
        # Un pooler en modo transacción no conserva el estado de sesión: nada de cursores del
        # servidor ni sentencias preparadas por psycopg, y no se envían timeouts de arranque
        # (hay que fijarlos a nivel de rol; ver docs/postgresql.md).
        config["DISABLE_SERVER_SIDE_CURSORS"] = True
        opciones.setdefault("prepare_threshold", None)
    else:
        opciones_servidor = fusionar_opciones_de_servidor(
            opciones.get("options", ""),
            {
                "lock_timeout": ajustes.lock_timeout,
                "statement_timeout": ajustes.statement_timeout,
                "idle_in_transaction_session_timeout": ajustes.idle_in_transaction_timeout,
            },
        )
        if opciones_servidor:
            opciones["options"] = opciones_servidor

    if opciones:
        config["OPTIONS"] = opciones

    return config


def fusionar_opciones_de_servidor(existentes, timeouts_en_segundos):
    """Agrega `-c nombre=milisegundos` solo para los parámetros que `existentes` no define."""
    existentes = (existentes or "").strip()
    definidos = set(re.findall(r"(?:-c\s*|--)([A-Za-z_]+)=", existentes))
    partes = [existentes] if existentes else []

    for nombre, segundos in timeouts_en_segundos.items():
        if segundos and nombre not in definidos:
            partes.append(f"-c {nombre}={segundos * 1000}")

    return " ".join(partes)
