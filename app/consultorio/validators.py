import re
from pathlib import Path

from django.core.exceptions import ValidationError


COLOR_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
EXTENSIONES_LOGO_PERMITIDAS = {".png", ".jpg", ".jpeg", ".webp"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def validar_color_hex(valor):
    if not valor or not COLOR_HEX_RE.fullmatch(valor):
        raise ValidationError("Ingresá un color HEX válido, por ejemplo #2563EB.")


def normalizar_color_hex(valor):
    validar_color_hex(valor)
    return valor.upper()


def validar_logo_consultorio(archivo):
    if not archivo:
        return

    extension = Path(archivo.name or "").suffix.lower()

    if extension not in EXTENSIONES_LOGO_PERMITIDAS:
        raise ValidationError("El logo debe ser PNG, JPG, JPEG o WEBP.")

    if extension == ".svg":
        raise ValidationError("No se permiten archivos SVG.")

    tamano = getattr(archivo, "size", 0) or 0

    if tamano > LOGO_MAX_BYTES:
        raise ValidationError("El logo no puede superar los 2 MB.")

    cabecera = _leer_cabecera(archivo)

    if not _cabecera_es_imagen_permitida(cabecera):
        raise ValidationError("El archivo no parece ser una imagen válida.")


def _leer_cabecera(archivo, bytes_a_leer=16):
    posicion = None

    try:
        if hasattr(archivo, "open"):
            archivo.open("rb")

        if hasattr(archivo, "tell"):
            posicion = archivo.tell()

        cabecera = archivo.read(bytes_a_leer)

        if posicion is not None and hasattr(archivo, "seek"):
            archivo.seek(posicion)

        return cabecera or b""
    except Exception:
        return b""


def _cabecera_es_imagen_permitida(cabecera):
    return (
        cabecera.startswith(b"\x89PNG\r\n\x1a\n")
        or cabecera.startswith(b"\xff\xd8\xff")
        or (len(cabecera) >= 12 and cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP")
    )
