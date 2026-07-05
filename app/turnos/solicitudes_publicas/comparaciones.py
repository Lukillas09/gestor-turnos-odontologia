from pacientes.normalizacion import (
    normalizar_documento,
    normalizar_email,
    normalizar_email_para_comparacion,
    normalizar_telefono,
    normalizar_texto_persona,
)


CAMPOS_COMPARABLES = ("documento", "nombre", "apellido", "telefono", "email")


def construir_fotografia_solicitud(datos):
    return {
        "documento_enviado": normalizar_documento(datos.get("documento")) or "",
        "nombre_enviado": datos.get("nombre", "").strip(),
        "apellido_enviado": datos.get("apellido", "").strip(),
        "telefono_enviado": datos.get("telefono", "").strip(),
        "email_enviado": datos.get("email", "").strip(),
        "motivo_enviado": datos.get("motivo", "").strip(),
    }


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
