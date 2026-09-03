from dataclasses import dataclass
from datetime import date, datetime, time

from django.utils.dateparse import parse_date, parse_time

from pacientes.normalizacion import (
    normalizar_documento,
    normalizar_email,
    normalizar_email_para_comparacion,
    normalizar_telefono,
    normalizar_texto_persona,
)

CAMPOS_COMPARABLES = ("documento", "nombre", "apellido", "telefono", "email")


@dataclass(frozen=True)
class IdentidadSolicitudPublica:
    documento_enviado: str
    nombre_enviado: str
    apellido_enviado: str
    telefono_enviado: str
    email_enviado: str
    motivo_enviado: str
    odontologo_id: int
    fecha: date
    hora_inicio: time
    tipo_turno_id: int | None


def construir_fotografia_solicitud(datos):
    return {
        "documento_enviado": normalizar_documento(datos.get("documento")) or "",
        "nombre_enviado": datos.get("nombre", "").strip(),
        "apellido_enviado": datos.get("apellido", "").strip(),
        "telefono_enviado": datos.get("telefono", "").strip(),
        "email_enviado": datos.get("email", "").strip(),
        "motivo_enviado": datos.get("motivo", "").strip(),
    }


def construir_identidad_solicitud_publica(datos, *, incluir_tipo_turno):
    fotografia = construir_fotografia_solicitud(datos)
    odontologo_id = _normalizar_pk(datos.get("odontologo"))
    fecha = _normalizar_fecha(datos.get("fecha"))
    hora_inicio = _normalizar_hora(datos.get("hora_inicio"))
    tipo_turno_id = _obtener_tipo_turno_id(datos) if incluir_tipo_turno else None

    if not fotografia["documento_enviado"] or not odontologo_id or not fecha or not hora_inicio:
        return None
    if incluir_tipo_turno and not tipo_turno_id:
        return None

    return IdentidadSolicitudPublica(
        documento_enviado=fotografia["documento_enviado"],
        nombre_enviado=normalizar_texto_persona(fotografia["nombre_enviado"]),
        apellido_enviado=normalizar_texto_persona(fotografia["apellido_enviado"]),
        telefono_enviado=normalizar_telefono(fotografia["telefono_enviado"]),
        email_enviado=normalizar_email_para_comparacion(fotografia["email_enviado"]),
        motivo_enviado=normalizar_texto_persona(fotografia["motivo_enviado"]),
        odontologo_id=odontologo_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        tipo_turno_id=tipo_turno_id,
    )


def construir_identidad_desde_solicitud(solicitud, *, incluir_tipo_turno):
    turno = solicitud.turno
    return construir_identidad_solicitud_publica(
        {
            "documento": solicitud.documento_enviado,
            "nombre": solicitud.nombre_enviado,
            "apellido": solicitud.apellido_enviado,
            "telefono": solicitud.telefono_enviado,
            "email": solicitud.email_enviado,
            "motivo": solicitud.motivo_enviado,
            "odontologo": turno.odontologo_id,
            "fecha": turno.fecha,
            "hora_inicio": turno.hora_inicio,
            "tipo_turno": solicitud.tipo_turno_id,
        },
        incluir_tipo_turno=incluir_tipo_turno,
    )


def _obtener_tipo_turno_id(datos):
    tipo_turno_id = _normalizar_pk(datos.get("tipo_turno"))
    if tipo_turno_id:
        return tipo_turno_id

    configuracion = datos.get("configuracion_tipo_turno")
    return _normalizar_pk(getattr(configuracion, "tipo_turno_id", None))


def _normalizar_pk(valor):
    valor = getattr(valor, "pk", valor)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _normalizar_fecha(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    return parse_date(str(valor or ""))


def _normalizar_hora(valor):
    if isinstance(valor, datetime):
        return valor.time()
    if isinstance(valor, time):
        return valor
    return parse_time(str(valor or ""))


def detectar_diferencias_datos_paciente(paciente, datos_enviados):
    diferencias = {}
    comparadores = {
        "documento": normalizar_documento,
        "nombre": normalizar_texto_persona,
        "apellido": normalizar_texto_persona,
        "telefono": normalizar_telefono,
        "email": normalizar_email,
    }

    for campo in CAMPOS_COMPARABLES:
        actual = getattr(paciente, campo) or ""
        enviado = datos_enviados.get(campo) or ""
        normalizador = comparadores[campo]

        if campo == "email":
            actual_normalizado = normalizar_email_para_comparacion(actual)
            enviado_normalizado = normalizar_email_para_comparacion(enviado)

            if actual_normalizado and not enviado_normalizado:
                continue

            if actual_normalizado == enviado_normalizado:
                continue

            diferencias[campo] = {
                "actual": actual,
                "enviado": enviado,
            }
            continue

        if (normalizador(actual) or "") == (normalizador(enviado) or ""):
            continue

        diferencias[campo] = {
            "actual": actual,
            "enviado": enviado,
        }

    return diferencias
