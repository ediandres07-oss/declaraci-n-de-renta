"""Checklist de documentos soporte para la declaración de renta.

Genera un PDF entregable al cliente con la lista de documentos que debe
preparar para el trámite, personalizado con su nombre y fecha límite.
"""
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

AZUL = colors.HexColor("#123f6b")
GRIS = colors.HexColor("#5a6b7f")

SECCIONES = [
    ("Básicos (indispensables)", [
        "Cédula de ciudadanía (copia legible por ambas caras).",
        "RUT actualizado con la responsabilidad de renta (si no lo tiene, lo gestionamos).",
        "Usuario y clave del portal DIAN, o disposición para crear la firma electrónica.",
        "Declaración de renta del año anterior, si declaró.",
    ]),
    ("Patrimonio a 31 de diciembre", [
        "Extractos bancarios de TODAS sus cuentas con el saldo a 31 de diciembre.",
        "Impuesto predial o escrituras de sus inmuebles.",
        "Tarjeta de propiedad de vehículos.",
        "Certificados de acciones, fiducias, inversiones o criptoactivos.",
        "Certificados de saldos de deudas a 31 de diciembre (hipoteca, créditos, tarjetas).",
    ]),
    ("Deducciones (le rebajan el impuesto)", [
        "Certificado de intereses de crédito de vivienda o leasing habitacional.",
        "Certificado de pagos de medicina prepagada o plan complementario de salud.",
        "Dependientes — hijos menores de 18: registro civil de nacimiento.",
        "Dependientes — hijos de 18 a 25 estudiando: registro civil + certificado de la "
        "institución educativa (educación financiada por usted).",
        "Dependientes — hijos mayores, cónyuge, padres o hermanos en dependencia física o "
        "psicológica: certificado médico.",
        "Dependientes — cónyuge, padres o hermanos sin ingresos: certificación de contador "
        "público de que sus ingresos del año fueron inferiores a 260 UVT.",
        "Certificados de aportes voluntarios AFC / pensiones voluntarias.",
        "Certificados de donaciones a entidades autorizadas.",
        "Certificado del 4×1000 (GMF) que expide su banco.",
    ]),
    ("Según su caso", [
        "Independientes: facturas emitidas en el año y planillas PILA.",
        "Pensionados: certificado de ingresos del fondo de pensiones o Colpensiones.",
        "Dividendos: certificado de la sociedad que los distribuyó.",
        "Venta de activos, herencias o premios: escrituras, documentos de la sucesión "
        "o certificados del premio.",
    ]),
]


def generar_checklist_pdf(ruta: Path, nombre: str = "",
                          fecha_limite: Optional[str] = None) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    base = getSampleStyleSheet()
    st_titulo = ParagraphStyle("t", parent=base["Title"], fontSize=15, textColor=AZUL)
    st_sub = ParagraphStyle("s", parent=base["Normal"], fontSize=9, textColor=GRIS)
    st_h = ParagraphStyle("h", parent=base["Heading2"], fontSize=11.5,
                          textColor=AZUL, spaceBefore=12, spaceAfter=4)
    st_item = ParagraphStyle("i", parent=base["Normal"], fontSize=9.5, leading=13.5)
    st_peq = ParagraphStyle("p", parent=base["Normal"], fontSize=7.5, textColor=GRIS)

    doc = SimpleDocTemplate(str(ruta), pagesize=letter, topMargin=18 * mm,
                            bottomMargin=15 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
                            title="Documentos para su declaración de renta")
    e = [Paragraph("Documentos para su declaración de renta", st_titulo)]
    saludo = f"Preparado para: <b>{nombre}</b> · " if nombre else ""
    limite = f"Su fecha límite de declaración: <b>{fecha_limite}</b> · " if fecha_limite else ""
    e.append(Paragraph(f"{saludo}{limite}Generado el {date.today().strftime('%d/%m/%Y')}", st_sub))
    e.append(Spacer(1, 4))
    e.append(HRFlowable(width="100%", color=AZUL, thickness=1.1))
    e.append(Spacer(1, 2))
    e.append(Paragraph(
        "La información que terceros reportan a la DIAN (exógena) ya la tenemos. "
        "Estos documentos la complementan y nos permiten declarar su realidad "
        "económica completa y aprovechar todas las deducciones a su favor. "
        "Envíe lo que aplique a su caso — si algo no aplica, simplemente omítalo.", st_item))

    for titulo, items in SECCIONES:
        e.append(Paragraph(titulo, st_h))
        filas = [[Paragraph("☐", st_item), Paragraph(txt, st_item)] for txt in items]
        t = Table(filas, colWidths=[16, None])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ]))
        e.append(t)

    e.append(Spacer(1, 14))
    e.append(HRFlowable(width="100%", color=colors.HexColor("#c9d4e2"), thickness=0.7))
    e.append(Paragraph(
        "Puede enviar fotos o PDF legibles por el medio acordado. Sus documentos se usan "
        "exclusivamente para elaborar y presentar su declaración de renta y no se comparten "
        "con terceros.", st_peq))
    doc.build(e)
    return ruta
