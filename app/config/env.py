import os


def cargar_env(path):
    if not path.exists():
        return

    for linea in path.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()

        if not linea or linea.startswith("#") or "=" not in linea:
            continue

        nombre, valor = linea.split("=", 1)
        nombre = nombre.strip()
        valor = valor.strip().strip('"').strip("'")

        if nombre:
            os.environ.setdefault(nombre, valor)


def env(nombre, default=""):
    return os.environ.get(nombre, default)


def env_bool(nombre, default=False):
    valor = os.environ.get(nombre)

    if valor is None:
        return default

    return valor.strip().lower() in {"1", "true", "yes", "on", "si"}


def env_list(nombre, default=None):
    valor = os.environ.get(nombre)

    if valor is None:
        return default or []

    return [item.strip() for item in valor.split(",") if item.strip()]
