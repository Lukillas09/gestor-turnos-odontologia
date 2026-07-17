import logging
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

AVISO_LEGAL = (
    "Este documento contiene indicaciones de cuidado brindadas por el profesional y no "
    "constituye una receta electrónica de medicamentos."
)
AVISO_URGENCIA = (
    "Ante síntomas inesperados, empeoramiento o una urgencia, comuníquese con el "
    "consultorio o con el servicio de salud correspondiente."
)


def generar_pdf_indicacion(indicacion, *, logo_bytes=None):
    _registrar_fuentes()
    salida = BytesIO()
    color_principal = _color_hex(indicacion.snapshot_consultorio.get("color_principal", "#2563EB"))
    estilos = _crear_estilos(color_principal)
    documento = SimpleDocTemplate(
        salida,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title="Indicaciones postoperatorias",
        author=indicacion.snapshot_profesional.get("nombre_completo", ""),
        subject="Documento clínico de indicaciones postoperatorias",
    )
    elementos = []
    elementos.extend(_encabezado(indicacion, estilos, color_principal, logo_bytes))
    elementos.append(Spacer(1, 5 * mm))
    elementos.append(Paragraph("INDICACIONES POSTOPERATORIAS", estilos["titulo_documento"]))
    elementos.append(Spacer(1, 2 * mm))
    elementos.append(
        _tabla_datos(
            (
                (
                    "Paciente",
                    indicacion.snapshot_paciente.get("nombre_completo", "-"),
                ),
                (
                    "Documento",
                    indicacion.snapshot_paciente.get("documento", "-") or "-",
                ),
                ("Fecha", _fecha_emision(indicacion)),
                (
                    "Profesional",
                    indicacion.snapshot_profesional.get("nombre_completo", "-"),
                ),
                (
                    "Matrícula",
                    indicacion.snapshot_profesional.get("matricula", "-") or "-",
                ),
                (
                    "Especialidad",
                    indicacion.snapshot_profesional.get("especialidad", "-") or "-",
                ),
            ),
            estilos,
        )
    )
    elementos.append(Spacer(1, 4 * mm))
    _agregar_seccion(
        elementos,
        "Procedimiento",
        indicacion.snapshot_documento.get("procedimiento", "") or "No especificado.",
        estilos,
    )
    _agregar_seccion(
        elementos,
        "Indicaciones",
        indicacion.snapshot_documento.get("contenido", ""),
        estilos,
    )
    _agregar_seccion(
        elementos,
        "Pautas de alarma",
        indicacion.snapshot_documento.get("pautas_alarma", "") or "No especificadas.",
        estilos,
    )
    _agregar_seccion(
        elementos,
        "Recomendaciones de control",
        indicacion.snapshot_documento.get("recomendaciones_control", "") or "No especificadas.",
        estilos,
    )
    _agregar_seccion(
        elementos,
        "Observaciones personalizadas",
        indicacion.snapshot_documento.get("observaciones_personalizadas", "")
        or "Sin observaciones adicionales.",
        estilos,
    )
    _agregar_seccion(
        elementos,
        "Próximo control",
        indicacion.snapshot_documento.get("proximo_control_display", "") or "No programado.",
        estilos,
    )
    elementos.append(Spacer(1, 2 * mm))
    contacto = _contacto_consultorio(indicacion.snapshot_consultorio)
    _agregar_seccion(elementos, "Contacto del consultorio", contacto, estilos)
    elementos.append(Spacer(1, 3 * mm))
    elementos.append(
        Table(
            [
                [
                    Paragraph("Estado del documento", estilos["meta_label"]),
                    Paragraph("Emitida por el profesional", estilos["meta_value"]),
                ],
                [
                    Paragraph("Referencia", estilos["meta_label"]),
                    Paragraph(str(indicacion.uuid)[:12], estilos["meta_value"]),
                ],
                [
                    Paragraph("Sello técnico abreviado", estilos["meta_label"]),
                    Paragraph(indicacion.referencia_integridad, estilos["meta_value"]),
                ],
            ],
            colWidths=[48 * mm, 112 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7FA")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#E2E8F0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            ),
        )
    )
    elementos.append(Spacer(1, 5 * mm))
    elementos.append(Paragraph(_html_seguro(AVISO_URGENCIA), estilos["aviso_urgencia"]))
    elementos.append(Spacer(1, 2 * mm))
    elementos.append(Paragraph(_html_seguro(AVISO_LEGAL), estilos["aviso_legal"]))

    def pie_pagina(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.setLineWidth(0.4)
        canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
        canvas.setFont("Vera", 7.5)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawString(18 * mm, 9 * mm, "Documento clínico de cuidado postoperatorio")
        canvas.drawRightString(
            A4[0] - 18 * mm,
            9 * mm,
            f"Página {doc.page}",
        )
        canvas.restoreState()

    documento.build(elementos, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
    return salida.getvalue()


def obtener_logo_bytes(configuracion):
    if not configuracion.logo:
        return None
    try:
        with configuracion.logo.open("rb") as archivo:
            return archivo.read()
    except Exception as error:
        logger.warning(
            "No se pudo incorporar el logo al PDF de indicaciones. error_type=%s",
            error.__class__.__name__,
        )
        return None


def _registrar_fuentes():
    if "Vera" in pdfmetrics.getRegisteredFontNames():
        return
    directorio_fuentes = Path(reportlab.__file__).resolve().parent / "fonts"
    pdfmetrics.registerFont(TTFont("Vera", directorio_fuentes / "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("Vera-Bold", directorio_fuentes / "VeraBd.ttf"))


def _crear_estilos(color_principal):
    base = getSampleStyleSheet()
    return {
        "consultorio": ParagraphStyle(
            "Consultorio",
            parent=base["Heading2"],
            fontName="Vera-Bold",
            fontSize=14,
            leading=17,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=2,
        ),
        "contacto": ParagraphStyle(
            "Contacto",
            parent=base["BodyText"],
            fontName="Vera",
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#475569"),
        ),
        "titulo_documento": ParagraphStyle(
            "TituloDocumento",
            parent=base["Title"],
            fontName="Vera-Bold",
            fontSize=18,
            leading=22,
            alignment=TA_CENTER,
            textColor=color_principal,
            spaceAfter=8,
        ),
        "seccion": ParagraphStyle(
            "Seccion",
            parent=base["Heading3"],
            fontName="Vera-Bold",
            fontSize=10.5,
            leading=13,
            textColor=colors.HexColor("#0F172A"),
            spaceBefore=5,
            spaceAfter=3,
        ),
        "cuerpo": ParagraphStyle(
            "Cuerpo",
            parent=base["BodyText"],
            fontName="Vera",
            fontSize=9.5,
            leading=14,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#1E293B"),
            splitLongWords=True,
        ),
        "dato_label": ParagraphStyle(
            "DatoLabel",
            parent=base["BodyText"],
            fontName="Vera-Bold",
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor("#64748B"),
        ),
        "dato_value": ParagraphStyle(
            "DatoValue",
            parent=base["BodyText"],
            fontName="Vera",
            fontSize=8.8,
            leading=11,
            textColor=colors.HexColor("#0F172A"),
        ),
        "meta_label": ParagraphStyle(
            "MetaLabel",
            parent=base["BodyText"],
            fontName="Vera-Bold",
            fontSize=7.5,
            textColor=colors.HexColor("#64748B"),
        ),
        "meta_value": ParagraphStyle(
            "MetaValue",
            parent=base["BodyText"],
            fontName="Vera",
            fontSize=8,
            textColor=colors.HexColor("#1E293B"),
        ),
        "aviso_urgencia": ParagraphStyle(
            "AvisoUrgencia",
            parent=base["BodyText"],
            fontName="Vera-Bold",
            fontSize=8.5,
            leading=12,
            borderColor=colors.HexColor("#F59E0B"),
            borderWidth=0.8,
            borderPadding=8,
            backColor=colors.HexColor("#FFFBEB"),
            textColor=colors.HexColor("#713F12"),
        ),
        "aviso_legal": ParagraphStyle(
            "AvisoLegal",
            parent=base["BodyText"],
            fontName="Vera",
            fontSize=7.8,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#475569"),
        ),
    }


def _encabezado(indicacion, estilos, color_principal, logo_bytes):
    consultorio = indicacion.snapshot_consultorio
    nombre = consultorio.get("nombre", "Consultorio odontológico")
    contacto = _contacto_consultorio(consultorio)
    marca = _imagen_logo(logo_bytes)
    if marca is None:
        iniciales = consultorio.get("iniciales", "CO")
        marca = Paragraph(
            f'<font color="white"><b>{_html_seguro(iniciales)}</b></font>',
            ParagraphStyle(
                "Iniciales",
                fontName="Vera-Bold",
                fontSize=13,
                leading=16,
                alignment=TA_CENTER,
                backColor=color_principal,
                borderPadding=8,
            ),
        )
    tabla = Table(
        [
            [
                marca,
                [
                    Paragraph(_html_seguro(nombre), estilos["consultorio"]),
                    Paragraph(_html_seguro(contacto), estilos["contacto"]),
                ],
            ]
        ],
        colWidths=[28 * mm, 132 * mm],
    )
    tabla.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [tabla, Spacer(1, 3 * mm), HRFlowable(color=color_principal, thickness=1.2)]


def _imagen_logo(logo_bytes):
    if not logo_bytes:
        return None
    try:
        lector = ImageReader(BytesIO(logo_bytes))
        ancho, alto = lector.getSize()
        factor = min((24 * mm) / ancho, (16 * mm) / alto)
        return Image(BytesIO(logo_bytes), width=ancho * factor, height=alto * factor)
    except Exception as error:
        logger.warning(
            "El logo no pudo interpretarse para el PDF de indicaciones. error_type=%s",
            error.__class__.__name__,
        )
        return None


def _tabla_datos(datos, estilos):
    filas = []
    for indice in range(0, len(datos), 2):
        izquierda = datos[indice]
        derecha = datos[indice + 1]
        filas.append(
            [
                Paragraph(_html_seguro(izquierda[0]), estilos["dato_label"]),
                Paragraph(_html_seguro(izquierda[1]), estilos["dato_value"]),
                Paragraph(_html_seguro(derecha[0]), estilos["dato_label"]),
                Paragraph(_html_seguro(derecha[1]), estilos["dato_value"]),
            ]
        )
    tabla = Table(filas, colWidths=[24 * mm, 56 * mm, 24 * mm, 56 * mm])
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return tabla


def _agregar_seccion(elementos, titulo, contenido, estilos):
    elementos.append(Paragraph(_html_seguro(titulo), estilos["seccion"]))
    elementos.append(Paragraph(_html_seguro(contenido), estilos["cuerpo"]))
    elementos.append(Spacer(1, 2 * mm))


def _html_seguro(valor):
    return escape(str(valor or "")).replace("\n", "<br/>")


def _color_hex(valor):
    try:
        return colors.HexColor(valor)
    except (TypeError, ValueError):
        return colors.HexColor("#2563EB")


def _fecha_emision(indicacion):
    if not indicacion.emitida_en:
        return "-"
    return timezone.localtime(indicacion.emitida_en).strftime("%d/%m/%Y %H:%M")


def _contacto_consultorio(snapshot):
    partes = [
        snapshot.get("direccion", ""),
        snapshot.get("telefono", ""),
        snapshot.get("whatsapp", ""),
        snapshot.get("email", ""),
    ]
    return " · ".join(str(parte).strip() for parte in partes if str(parte).strip()) or "-"
