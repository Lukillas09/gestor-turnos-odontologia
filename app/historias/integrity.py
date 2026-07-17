import hashlib
import hmac
import json
from datetime import UTC, datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class IntegridadClinicaError(RuntimeError):
    pass


def serializar_json_canonico(datos):
    return json.dumps(
        datos,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def normalizar_instante(instante):
    if not isinstance(instante, datetime):
        raise TypeError("Se esperaba una fecha y hora para el sello de integridad.")
    if instante.tzinfo is None:
        raise ValueError("La fecha y hora del sello debe incluir zona horaria.")
    return instante.astimezone(UTC).isoformat().replace("+00:00", "Z")


def crear_sello_version(
    *,
    historia_id,
    numero_version,
    snapshot,
    creado_por_id,
    creado_en,
    motivo,
    hash_anterior,
):
    return calcular_sello_integridad(
        {
            "tipo": "historia_clinica_version",
            "historia_id": historia_id,
            "numero_version": numero_version,
            "snapshot": snapshot,
            "creado_por_id": creado_por_id,
            "creado_en": normalizar_instante(creado_en),
            "motivo": motivo,
            "hash_anterior": hash_anterior,
        }
    )


def crear_sello_enmienda(
    *,
    historia_id,
    numero_enmienda,
    texto,
    motivo,
    odontologo_id,
    creado_por_id,
    creado_en,
    hash_anterior,
):
    return calcular_sello_integridad(
        {
            "tipo": "historia_clinica_enmienda",
            "historia_id": historia_id,
            "numero_enmienda": numero_enmienda,
            "texto": texto,
            "motivo": motivo,
            "odontologo_id": odontologo_id,
            "creado_por_id": creado_por_id,
            "creado_en": normalizar_instante(creado_en),
            "hash_anterior": hash_anterior,
        }
    )


def calcular_sello_integridad(datos):
    clave = _obtener_clave_integridad()
    return hmac.new(clave, serializar_json_canonico(datos), hashlib.sha256).hexdigest()


def sellos_coinciden(esperado, observado):
    return hmac.compare_digest(esperado or "", observado or "")


def _obtener_clave_integridad():
    if not getattr(settings, "CLINICAL_INTEGRITY_ENABLED", True):
        raise IntegridadClinicaError("El sistema de integridad clínica está deshabilitado.")

    clave = getattr(settings, "CLINICAL_INTEGRITY_HMAC_KEY", "")
    if not clave:
        raise ImproperlyConfigured(
            "CLINICAL_INTEGRITY_HMAC_KEY es necesaria para operar con registros clínicos."
        )

    return clave.encode("utf-8")
