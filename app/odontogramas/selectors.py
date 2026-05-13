from .domain import CARAS_DENTALES, FILAS_ODONTOGRAMA


def estados_activos_por_cara(odontograma):
    estados = odontograma.estados_dentales.filter(activo=True).select_related(
        "odontologo",
        "odontologo__usuario",
    )
    return {(estado.diente, estado.cara): estado for estado in estados}


def construir_filas_odontograma(odontograma):
    estados = estados_activos_por_cara(odontograma)
    filas = []

    for fila in FILAS_ODONTOGRAMA:
        grupos = []

        for grupo in fila["grupos"]:
            dientes = []

            for numero in grupo:
                caras = []

                for cara, etiqueta in CARAS_DENTALES.items():
                    estado = estados.get((numero, cara))
                    caras.append(
                        {
                            "codigo": cara,
                            "etiqueta": etiqueta,
                            "estado": estado,
                            "tooltip": construir_tooltip(numero, etiqueta, estado),
                        }
                    )

                dientes.append(
                    {
                        "numero": numero,
                        "caras": caras,
                    }
                )

            grupos.append(dientes)

        filas.append({**fila, "grupos": grupos})

    return filas


def construir_tooltip(diente, cara_display, estado):
    if estado is None:
        return f"{diente} - {cara_display}\nSin estado registrado"

    partes = [
        f"{diente} - {cara_display}",
        estado.get_estado_clinico_display(),
        "Realizado" if estado.realizado else "Pendiente",
    ]

    if estado.odontologo:
        partes.append(str(estado.odontologo))

    partes.append(estado.fecha.strftime("%d/%m/%Y"))

    if estado.observacion:
        partes.append(estado.observacion[:120])

    return "\n".join(partes)
