"""Resumen ejecutivo en PDF para entregar al cliente.

Genera un documento de 1-2 páginas con: datos del declarante, obligación de
declarar, composición por cédulas, liquidación del impuesto, resultado
(saldo a pagar / a favor) y advertencias, con el aviso de borrador.
"""
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .modelos import DatosDeclaracion, Liquidacion, ResultadoExogena
from .parametros import Parametros

AZUL = colors.HexColor("#1a4f8b")
GRIS = colors.HexColor("#f0f4f9")
ROJO = colors.HexColor("#b3372f")
VERDE = colors.HexColor("#1e7d43")


def _fmt(v: float) -> str:
    return f"${v:,.0f}".replace(",", ".")


def _estilos():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("t", parent=base["Title"], fontSize=15,
                                 textColor=AZUL, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=8.5,
                              textColor=colors.HexColor("#5a6b7f"),
                              alignment=TA_CENTER, spaceAfter=10),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11,
                             textColor=AZUL, spaceBefore=10, spaceAfter=4),
        "normal": ParagraphStyle("n", parent=base["Normal"], fontSize=9, leading=12),
        "peq": ParagraphStyle("p", parent=base["Normal"], fontSize=7.5,
                              textColor=colors.HexColor("#5a6b7f"), leading=9.5),
        "alerta": ParagraphStyle("a", parent=base["Normal"], fontSize=8.5,
                                 textColor=ROJO, leading=11),
        "kpi": ParagraphStyle("k", parent=base["Normal"], fontSize=13,
                              alignment=TA_RIGHT, leading=15),
    }


def _tabla(filas, anchos, negrilla_ultima=False, resaltar=None):
    t = Table(filas, colWidths=anchos)
    estilo = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), AZUL),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRIS]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d4e2")),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if negrilla_ultima:
        estilo += [("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                   ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#dfe9f5"))]
    if resaltar is not None:
        estilo += [("TEXTCOLOR", (1, resaltar), (1, resaltar),
                    ROJO if "pagar" in str(filas[resaltar][0]).lower() else VERDE)]
    t.setStyle(TableStyle(estilo))
    return t


def generar_resumen_pdf(
    ruta: Path,
    datos: DatosDeclaracion,
    liq: Liquidacion,
    p: Parametros,
    exogena: Optional[ResultadoExogena] = None,
    razones_obligado=None,
    preparado_por: str = "",
) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    st = _estilos()
    R = liq.r
    con = datos.contribuyente
    nombre = " ".join(x for x in (con.primer_nombre, con.otros_nombres,
                                  con.primer_apellido, con.segundo_apellido) if x) \
             or (exogena.nombre if exogena else "")

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=16 * mm, bottomMargin=14 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title=f"Resumen ejecutivo renta AG {p.anio_gravable}")
    e = []

    # ---------------- encabezado ----------------
    e.append(Paragraph("Resumen Ejecutivo — Declaración de Renta", st["titulo"]))
    e.append(Paragraph(
        f"Formulario 210 · Personas Naturales Residentes · Año Gravable {p.anio_gravable} · "
        f"UVT {_fmt(p.uvt)} · Preparado el {date.today().strftime('%d/%m/%Y')}"
        + (f" · {preparado_por}" if preparado_por else ""), st["sub"]))
    e.append(HRFlowable(width="100%", color=AZUL, thickness=1.2))

    # ---------------- declarante ----------------
    e.append(Paragraph("1. Datos del declarante", st["h2"]))
    e.append(_tabla([
        ["Contribuyente", "Identificación", "DV", "Actividad económica", "Dependientes"],
        [nombre or "—", con.nit or "—", con.dv or "—",
         con.actividad_economica or "—", str(datos.dependientes)],
    ], [None, 90, 30, 95, 70]))

    # ---------------- obligación ----------------
    if razones_obligado:
        e.append(Paragraph("2. Obligación de declarar", st["h2"]))
        e.append(Paragraph(
            "Según la información exógena reportada por terceros a la DIAN, el "
            "contribuyente <b>está obligado a declarar</b> por:", st["normal"]))
        for r in razones_obligado:
            e.append(Paragraph(f"• {r}", st["normal"]))

    # ---------------- patrimonio ----------------
    e.append(Paragraph("3. Patrimonio", st["h2"]))
    e.append(_tabla([
        ["Concepto", "Renglón", "Valor"],
        ["Patrimonio bruto", "29", _fmt(R(29))],
        ["Deudas", "30", _fmt(R(30))],
        ["Patrimonio líquido", "31", _fmt(R(31))],
    ], [None, 60, 110], negrilla_ultima=True))

    # ---------------- rentas por cédula ----------------
    e.append(Paragraph("4. Composición de las rentas", st["h2"]))
    filas = [["Cédula / concepto", "Ingresos", "INCRNGO / costos", "Exentas y deduc.", "Renta líquida"]]
    def _fila(nombre_c, ing, incr, exen, liquida):
        filas.append([nombre_c, _fmt(ing), _fmt(incr), _fmt(exen), _fmt(liquida)])
    _fila("Rentas de trabajo", R(32), R(33), R(41), R(42))
    if R(43): _fila("Honorarios", R(43), R(44) + R(45), R(53), R(57))
    if R(58): _fila("Rentas de capital", R(58), R(59) + R(60), R(69), R(73))
    if R(74): _fila("Rentas no laborales", R(74), R(76) + R(77), R(86), R(90))
    if R(99): _fila("Pensiones", R(99), R(100), R(102), R(103))
    div = R(104) + R(107) + R(108) + R(109)
    if div: _fila("Dividendos", div, R(105), R(110), R(106) + R(107) + R(108))
    filas.append(["Renta líquida gravable (cédula general + pensiones)", "", "",
                  "", _fmt(R(97) + R(103))])
    e.append(_tabla(filas, [None, 78, 82, 80, 84], negrilla_ultima=True))
    e.append(Paragraph(
        f"Límite de rentas exentas y deducciones aplicado (Art. 336 E.T.): menor entre el 40% de la "
        f"base y 1.340 UVT ({_fmt(p.a_pesos(1340))}). Deducción por dependientes (R139): {_fmt(R(139))}. "
        f"Deducción 1% factura electrónica (R28): {_fmt(R(28))}.", st["peq"]))

    # ---------------- liquidación ----------------
    e.append(Paragraph("5. Liquidación del impuesto", st["h2"]))
    filas = [["Concepto", "Renglón", "Valor"],
             ["Impuesto sobre rentas líquidas (tabla Art. 241)", "116/117", _fmt(R(116) + R(117))]]
    if R(118) + R(119) + R(120):
        filas.append(["Impuesto sobre dividendos", "118–120", _fmt(R(118) + R(119) + R(120))])
    if R(125):
        filas.append(["(−) Descuentos tributarios", "125", _fmt(R(125))])
    filas.append(["Impuesto neto de renta", "126", _fmt(R(126))])
    if R(127):
        filas.append(["Impuesto de ganancias ocasionales", "127", _fmt(R(127))])
    filas.append(["Total impuesto a cargo", "129", _fmt(R(129))])
    filas += [
        ["(−) Retenciones que le practicaron", "132", _fmt(R(132))],
        ["(−) Saldo a favor y anticipo del año anterior", "130–131", _fmt(R(130) + R(131))],
        ["(+) Anticipo de renta año siguiente", "133", _fmt(R(133))],
    ]
    if R(135):
        filas.append(["(+) Sanciones", "135", _fmt(R(135))])
    e.append(_tabla(filas, [None, 70, 110]))
    e.append(Spacer(1, 8))

    # ---------------- resultado ----------------
    a_pagar = R(136) > 0
    color = ROJO if a_pagar else VERDE
    texto = ("TOTAL SALDO A PAGAR (R136): " + _fmt(R(136))) if a_pagar else \
            ("TOTAL SALDO A FAVOR (R137): " + _fmt(R(137)))
    caja = Table([[Paragraph(f"<b>{texto}</b>",
                             ParagraphStyle("res", fontSize=13, textColor=colors.white,
                                            alignment=TA_CENTER, leading=17))]],
                 colWidths=[None])
    caja.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color),
                              ("TOPPADDING", (0, 0), (-1, -1), 9),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                              ("ROUNDEDCORNERS", [6, 6, 6, 6])]))
    e.append(caja)

    # ---------------- advertencias y notas ----------------
    if liq.advertencias or (exogena and exogena.advertencias):
        e.append(Paragraph("6. Puntos de atención", st["h2"]))
        for a in (liq.advertencias + (exogena.advertencias if exogena else []))[:10]:
            e.append(Paragraph(f"⚠ {a}", st["alerta"]))

    e.append(Spacer(1, 12))
    e.append(HRFlowable(width="100%", color=colors.HexColor("#c9d4e2"), thickness=0.7))
    e.append(Paragraph(
        "Este documento es un BORRADOR de apoyo elaborado a partir de la información exógena "
        "reportada por terceros a la DIAN y los datos suministrados por el contribuyente. No "
        "constituye asesoría tributaria ni reemplaza el criterio de un contador público o abogado "
        "tributarista. Verifique la UVT, topes, tarifas y el componente inflacionario contra la "
        "normativa vigente antes de presentar la declaración. La información exógena no es "
        "indispensable ni exonera de declarar la totalidad de la realidad económica.", st["peq"]))

    doc.build(e)
    return ruta
