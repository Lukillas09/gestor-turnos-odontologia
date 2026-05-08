from urllib.parse import parse_qsl, unquote, urlparse


POSTGRES_SCHEMES = {"postgres", "postgresql"}
SQLITE_SCHEMES = {"sqlite", "sqlite3"}


def configurar_base_de_datos(database_url, base_dir):
    if not database_url:
        return configurar_sqlite_por_defecto(base_dir)

    url = urlparse(database_url)
    esquema = url.scheme.lower()

    if esquema in SQLITE_SCHEMES:
        return configurar_sqlite_desde_url(url, base_dir)

    if esquema in POSTGRES_SCHEMES:
        return configurar_postgres_desde_url(url)

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


def configurar_postgres_desde_url(url):
    nombre_base = unquote(url.path.lstrip("/"))

    if not nombre_base:
        raise RuntimeError("DATABASE_URL debe incluir el nombre de la base PostgreSQL.")

    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": nombre_base,
    }

    if url.username:
        config["USER"] = unquote(url.username)

    if url.password:
        config["PASSWORD"] = unquote(url.password)

    if url.hostname:
        config["HOST"] = url.hostname

    if url.port:
        config["PORT"] = str(url.port)

    opciones = dict(parse_qsl(url.query, keep_blank_values=False))

    if opciones:
        config["OPTIONS"] = opciones

    return config
