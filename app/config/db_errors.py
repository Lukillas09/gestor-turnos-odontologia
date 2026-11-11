from django.db import OperationalError

# Esperas que la base corta por sí misma y que no indican un dato inválido ni un bug:
# lock_not_available (lock_timeout), query_canceled (statement_timeout) y deadlock_detected.
SQLSTATES_ESPERA_AGOTADA = frozenset({"55P03", "57014", "40P01"})


def sqlstate_de(error):
    """SQLSTATE del error original de psycopg, o "" si no hay (por ejemplo, en SQLite)."""
    causa = getattr(error, "__cause__", None)
    return getattr(causa, "sqlstate", None) or ""


def es_espera_de_base_agotada(error):
    return isinstance(error, OperationalError) and sqlstate_de(error) in SQLSTATES_ESPERA_AGOTADA
