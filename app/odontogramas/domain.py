CARAS_DENTALES = {
    "vestibular": "Vestibular",
    "lingual_palatina": "Lingual / palatina",
    "mesial": "Mesial",
    "distal": "Distal",
    "oclusal_incisal": "Oclusal / incisal",
}

DIENTES_PERMANENTES = (
    18,
    17,
    16,
    15,
    14,
    13,
    12,
    11,
    21,
    22,
    23,
    24,
    25,
    26,
    27,
    28,
    48,
    47,
    46,
    45,
    44,
    43,
    42,
    41,
    31,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
)

DIENTES_TEMPORALES = (
    55,
    54,
    53,
    52,
    51,
    61,
    62,
    63,
    64,
    65,
    85,
    84,
    83,
    82,
    81,
    71,
    72,
    73,
    74,
    75,
)

DIENTES_FDI = DIENTES_PERMANENTES + DIENTES_TEMPORALES

FILAS_ODONTOGRAMA = [
    {
        "titulo": "Dentición permanente superior",
        "tipo": "permanente",
        "lado_inicio": "Der",
        "lado_fin": "Izq",
        "grupos": [
            [18, 17, 16, 15, 14, 13, 12, 11],
            [21, 22, 23, 24, 25, 26, 27, 28],
        ],
    },
    {
        "titulo": "Dentición permanente inferior",
        "tipo": "permanente",
        "lado_inicio": "Der",
        "lado_fin": "Izq",
        "grupos": [
            [48, 47, 46, 45, 44, 43, 42, 41],
            [31, 32, 33, 34, 35, 36, 37, 38],
        ],
    },
    {
        "titulo": "Dentición temporal superior",
        "tipo": "temporal",
        "lado_inicio": "Der",
        "lado_fin": "Izq",
        "grupos": [
            [55, 54, 53, 52, 51],
            [61, 62, 63, 64, 65],
        ],
    },
    {
        "titulo": "Dentición temporal inferior",
        "tipo": "temporal",
        "lado_inicio": "Der",
        "lado_fin": "Izq",
        "grupos": [
            [85, 84, 83, 82, 81],
            [71, 72, 73, 74, 75],
        ],
    },
]

COLORES_HEX = {
    "azul": "#2563eb",
    "rojo": "#dc2626",
    "verde": "#16a34a",
    "negro": "#111827",
    "neutro": "#ffffff",
}

COLOR_POR_ESTADO = {
    "sano": "neutro",
    "caries": "rojo",
    "restauracion_necesaria": "rojo",
    "extraccion_indicada": "rojo",
    "obturacion": "azul",
    "corona": "azul",
    "implante": "azul",
    "conducto": "azul",
    "protesis": "azul",
    "sellador": "verde",
    "temporal": "verde",
    "control": "verde",
    "ausente": "negro",
    "extraido": "negro",
    "fractura": "negro",
    "observacion_especial": "negro",
}


def color_para_estado(estado):
    return COLOR_POR_ESTADO.get(estado, "neutro")


def color_hex(color):
    return COLORES_HEX.get(color, COLORES_HEX["neutro"])
