"""Guía entregable: cómo presentar el Formulario 210 en el portal DIAN.

Acompaña al borrador (Formulario 210 en PDF) del plan económico. Con el plan
PDF el cliente recibe la declaración diligenciada y la sube él mismo a la DIAN;
esta guía explica ese paso a paso. No incluye datos sensibles: es genérica y se
personaliza únicamente con el nombre y la fecha límite si están disponibles.
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

PASOS = [
    ("1. Ten a la mano tu borrador",
     "En este PDF está tu Formulario 210 diligenciado renglón por renglón. "
     "Cada casilla tiene su número (por ejemplo, 29, 36, 65…). Vas a copiar "
     "esos mismos valores en el formulario oficial de la DIAN."),
    ("2. Entra al portal de la DIAN",
     "Ingresa a <b>www.dian.gov.co</b> → <b>Usuario Registrado</b> y entra con "
     "tu tipo de documento, número y clave. Si es tu primera vez, crea tu cuenta "
     "y habilita la <b>firma electrónica (IFE)</b> desde el mismo portal."),
    ("3. Abre el Formulario 210",
     "Dentro del portal ve a <b>Diligenciar / Presentar</b> → busca el "
     "<b>Formulario 210</b> (Declaración de Renta y Complementarios Personas "
     "Naturales) → selecciona el <b>año gravable</b> correcto y crea un nuevo "
     "borrador."),
    ("4. Transcribe casilla por casilla",
     "Copia en el formulario de la DIAN cada valor de este PDF, guiándote por el "
     "número de casilla. El portal calcula solo los totales y las casillas "
     "sombreadas; escribe únicamente las que traen valor aquí. Verifica que los "
     "totales coincidan con los de tu borrador."),
    ("5. Guarda y revisa",
     "Usa <b>Guardar</b> con frecuencia. Antes de enviar, revisa que el "
     "impuesto a cargo o el saldo a favor sea igual al de tu borrador. Si algo "
     "no cuadra, escríbenos y lo revisamos contigo."),
    ("6. Firma y presenta",
     "Cuando todo esté correcto, presiona <b>Firmar</b> (con tu firma "
     "electrónica) y luego <b>Presentar</b>. El portal genera el formulario "
     "definitivo con su número de radicado: guárdalo."),
    ("7. Paga si tienes saldo a cargo",
     "Si tu declaración arroja impuesto a pagar, genera el <b>recibo de pago "
     "480</b> desde el portal y págalo por PSE o en el banco antes de tu fecha "
     "límite. Si te da saldo a favor o en ceros, con presentar es suficiente."),
]


def generar_guia_dian_pdf(ruta: Path, nombre: str = "",
                          fecha_limite: Optional[str] = None) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    base = getSampleStyleSheet()
    st_titulo = ParagraphStyle("t", parent=base["Title"], fontSize=15, textColor=AZUL)
    st_sub = ParagraphStyle("s", parent=base["Normal"], fontSize=9, textColor=GRIS)
    st_intro = ParagraphStyle("in", parent=base["Normal"], fontSize=9.5, leading=13.5)
    st_h = ParagraphStyle("h", parent=base["Heading2"], fontSize=11.5,
                          textColor=AZUL, spaceBefore=4, spaceAfter=2)
    st_item = ParagraphStyle("i", parent=base["Normal"], fontSize=9.5, leading=13.5)
    st_peq = ParagraphStyle("p", parent=base["Normal"], fontSize=7.5, textColor=GRIS)

    doc = SimpleDocTemplate(str(ruta), pagesize=letter, topMargin=18 * mm,
                            bottomMargin=15 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
                            title="Cómo presentar tu Formulario 210 en la DIAN")
    e = [Paragraph("Cómo presentar tu Formulario 210 en la DIAN", st_titulo)]
    saludo = f"Preparado para: <b>{nombre}</b> · " if nombre else ""
    limite = f"Tu fecha límite: <b>{fecha_limite}</b> · " if fecha_limite else ""
    e.append(Paragraph(f"{saludo}{limite}Generado el {date.today().strftime('%d/%m/%Y')}", st_sub))
    e.append(Spacer(1, 4))
    e.append(HRFlowable(width="100%", color=AZUL, thickness=1.1))
    e.append(Spacer(1, 4))
    e.append(Paragraph(
        "Con tu plan recibes el <b>borrador de tu declaración</b> (Formulario 210 "
        "diligenciado renglón por renglón). Tú la subes al portal de la DIAN "
        "siguiendo estos pasos. Si en cualquier punto tienes dudas, escríbenos y "
        "te ayudamos.", st_intro))
    e.append(Spacer(1, 6))

    for titulo, texto in PASOS:
        e.append(Paragraph(titulo, st_h))
        e.append(Paragraph(texto, st_item))

    e.append(Spacer(1, 12))
    e.append(HRFlowable(width="100%", color=colors.HexColor("#c9d4e2"), thickness=0.7))
    e.append(Paragraph(
        "¿No quieres hacer este paso tú mismo? Con el plan de presentación nosotros "
        "montamos tu declaración en el portal DIAN y la presentamos por ti. "
        "Este documento es una guía de apoyo; verifica siempre la normativa vigente "
        "y los plazos oficiales de la DIAN.", st_peq))
    doc.build(e)
    return ruta
