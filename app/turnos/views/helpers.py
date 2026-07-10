CAMPO_REVISION_PUBLICA_LABELS = (
    ("nombre", "Nombre"),
    ("apellido", "Apellido"),
    ("telefono", "Teléfono"),
    ("email", "Email"),
)


def construir_filas_revision_solicitud_publica(solicitud):
    paciente = solicitud.paciente
    diferencias = set((solicitud.diferencias_detectadas or {}).keys())
    filas = []

    for orden, (campo, etiqueta) in enumerate(CAMPO_REVISION_PUBLICA_LABELS, start=1):
        filas.append(
            {
                "campo": campo,
                "etiqueta": etiqueta,
                "actual": getattr(paciente, campo) or "-",
                "enviado": getattr(solicitud, f"{campo}_enviado") or "-",
                "diferente": campo in diferencias,
                "orden": orden,
            }
        )

    return sorted(filas, key=lambda fila: (not fila["diferente"], fila["orden"]))
