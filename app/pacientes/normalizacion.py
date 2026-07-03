import re


def normalizar_documento(documento):
    if documento is None:
        return None

    documento = re.sub(r"[\s.\-]", "", str(documento).strip())
    return documento or None


def normalizar_texto_persona(valor):
    valor = " ".join(str(valor or "").strip().split())
    return valor.casefold()


def normalizar_email(valor):
    return str(valor or "").strip().casefold()


def normalizar_telefono(valor):
    return re.sub(r"[\s\-().+]", "", str(valor or ""))
