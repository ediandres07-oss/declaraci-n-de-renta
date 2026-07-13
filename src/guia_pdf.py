"""Genera la guía-obsequio en PDF: "Prepárate para declarar tu renta".

Es el lead magnet que se entrega a cambio del correo (lista de espera). No
depende de la DIAN: reúne el checklist de documentos, la tabla de fechas límite
por cédula y el paso a paso para descargar la exógena cuando la DIAN la habilite.

Se genera con reportlab (misma librería que el resto de PDFs de la app). El
script `scripts` de abajo lo escribe en static/ para servirlo como archivo fijo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

VERDE = colors.HexColor("#2e8f77")
VERDE_OSC = colors.HexColor("#227a63")
TINTA = colors.HexColor("#1e2b3a")
GRIS = colors.HexColor("#5a6b7f")
DORADO = colors.HexColor("#e8a413")
FONDO = colors.HexColor("#f5f8f7")

_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

DOCUMENTOS = [
    "Certificados de ingresos y retenciones (laborales y por honorarios).",
    "Certificados de rendimientos financieros de tus bancos y CDT.",
    "Certificados de aportes a salud, pensión, AFC o pensiones voluntarias.",
    "Intereses de crédito de vivienda o de ICETEX (si aplican).",
    "Pagos de medicina prepagada o seguros de salud.",
    "Datos de tus dependientes: hijos, cónyuge o padres a cargo (rebajan el impuesto).",
    "Escrituras y avalúos de inmuebles y vehículos (para tu patrimonio).",
    "Certificados de acciones, fondos o inversiones.",
    "Tu RUT actualizado y tu usuario y contraseña de la DIAN.",
]

PASOS_EXOGENA = [
    "Entra a <b>www.dian.gov.co</b> y abre <b>“Usuarios registrados”</b> (plataforma MUISCA).",
    "Ingresa con tu <b>número de cédula</b> marcando <b>“A nombre propio”</b>. Si es tu primera vez, "
    "habilita tu usuario con tu RUT.",
    "Busca <b>“Consulta de información exógena reportada por terceros”</b> "
    "(escribe “exógena” en el buscador del portal).",
    "Elige el <b>año gravable 2025</b> y descarga el archivo <b>Excel (.xlsx)</b>.",
    "Súbelo en <b>tributando.co</b> y conoce gratis si debes declarar, tu fecha límite y "
    "cuánto pagarías.",
]


def _casilla() -> Drawing:
    """Casilla cuadrada vacía (para marcar), de tamaño fijo, en verde de marca."""
    d = Drawing(4.2 * mm, 4.2 * mm)
    d.add(Rect(0.3 * mm, 0, 3.6 * mm, 3.6 * mm, rx=0.6 * mm, ry=0.6 * mm,
               strokeColor=VERDE, strokeWidth=1, fillColor=None))
    return d


def _checklist(items: list, estilo: ParagraphStyle) -> Table:
    filas = [[_casilla(), Paragraph(t, estilo)] for t in items]
    t = Table(filas, colWidths=[8 * mm, 158 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    return t


def _fecha_txt(f: date) -> str:
    anio = max(f.year, 2026)   # normaliza un dato viejo de la plantilla (evita mostrar 2025)
    return f"{f.day} de {_MESES[f.month]} de {anio}"


def _tabla_fechas(calendario: dict) -> Table:
    """Arma la tabla 'últimos 2 dígitos de la cédula → fecha límite' en 2 columnas."""
    porfecha = defaultdict(list)
    for dig, f in calendario.items():
        porfecha[f].append(dig)
    filas_datos = []
    for f in sorted(porfecha):
        ds = sorted(porfecha[f])
        rango = ds[0] if len(ds) == 1 else f"{ds[0]} y {ds[-1]}"
        filas_datos.append((rango, _fecha_txt(f)))

    encabezado = ["Terminación de cédula", "Fecha límite", "Terminación de cédula", "Fecha límite"]
    mitad = (len(filas_datos) + 1) // 2
    izq, der = filas_datos[:mitad], filas_datos[mitad:]
    cuerpo = []
    for i in range(mitad):
        a = izq[i]
        b = der[i] if i < len(der) else ("", "")
        cuerpo.append([a[0], a[1], b[0], b[1]])

    est = ParagraphStyle("cel", fontName="Helvetica", fontSize=8.2, textColor=TINTA, leading=10)
    est_h = ParagraphStyle("celh", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white, leading=10)
    data = [[Paragraph(c, est_h) for c in encabezado]]
    data += [[Paragraph(str(c), est) for c in fila] for fila in cuerpo]

    t = Table(data, colWidths=[30 * mm, 32 * mm, 30 * mm, 32 * mm], hAlign="LEFT")
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), VERDE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dbe3ec")),
        ("LINEAFTER", (1, 0), (1, -1), 0.8, colors.HexColor("#c7d3df")),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            estilo.append(("BACKGROUND", (0, r), (-1, r), FONDO))
    t.setStyle(TableStyle(estilo))
    return t


def generar_guia_pdf(salida: Path, calendario: dict, negocio: dict | None = None,
                     anio: int = 2025) -> Path:
    negocio = negocio or {}
    whatsapp = negocio.get("whatsapp", "")
    correo = negocio.get("correo", "")

    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, textColor=TINTA, leading=23, spaceAfter=2)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, textColor=GRIS, leading=15, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, textColor=VERDE_OSC, leading=16,
                        spaceBefore=14, spaceAfter=7)
    item = ParagraphStyle("item", fontName="Helvetica", fontSize=10, textColor=TINTA, leading=15, leftIndent=16,
                          spaceAfter=5)
    nota = ParagraphStyle("nota", fontName="Helvetica-Oblique", fontSize=9.5, textColor=VERDE_OSC, leading=13)
    pie = ParagraphStyle("pie", fontName="Helvetica", fontSize=8.5, textColor=GRIS, leading=12)

    doc = SimpleDocTemplate(str(salida), pagesize=A4, topMargin=16 * mm, bottomMargin=14 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            title=f"Prepárate para declarar tu renta {anio}", author="tributando.co")
    flow = []
    flow.append(Paragraph("tributando.co", ParagraphStyle("marca", fontName="Helvetica-Bold",
                          fontSize=12, textColor=VERDE, spaceAfter=10)))
    flow.append(Paragraph(f"Prepárate para declarar tu renta {anio}", h1))
    flow.append(Paragraph("Tu guía rápida para llegar listo, sin afanes ni sanciones. Reúne esto con tiempo "
                          "y declara en minutos apenas la DIAN habilite tu información.", sub))

    flow.append(Paragraph("1 · Documentos que debes reunir", h2))
    item_lista = ParagraphStyle("itemlista", fontName="Helvetica", fontSize=10, textColor=TINTA, leading=14)
    flow.append(_checklist(DOCUMENTOS, item_lista))

    flow.append(Paragraph("2 · Tu fecha límite según tu cédula", h2))
    flow.append(Paragraph("La fecha de vencimiento depende de los <b>dos últimos dígitos de tu cédula o NIT</b>. "
                          "Búscala en la tabla y anótala:", sub))
    flow.append(_tabla_fechas(calendario))
    flow.append(Spacer(1, 4 * mm))
    flow.append(Paragraph("Declarar después de tu fecha genera <b>sanción por extemporaneidad</b> más intereses. "
                          "Mejor con tiempo.", nota))

    flow.append(Paragraph("3 · Cómo descargar tu exógena de la DIAN", h2))
    flow.append(Paragraph("El archivo de “información exógena” es gratis y es todo lo que necesitas para "
                          "empezar. Cuando la DIAN habilite la consulta del año gravable 2025:", sub))
    for i, p in enumerate(PASOS_EXOGENA, 1):
        flow.append(Paragraph(f"<b>{i}.</b>  {p}", item))

    flow.append(Spacer(1, 8 * mm))
    contacto = []
    if whatsapp:
        contacto.append(f"WhatsApp {whatsapp}")
    if correo:
        contacto.append(correo)
    contacto_txt = "  ·  ".join(contacto)
    flow.append(Paragraph("¿Dudas? Escríbenos y te ayudamos:  " + contacto_txt if contacto_txt
                          else "tributando.co", pie))
    flow.append(Paragraph("Esta guía es informativa y no reemplaza la asesoría tributaria personalizada.  "
                          "tributando.co", pie))

    doc.build(flow)
    return salida
