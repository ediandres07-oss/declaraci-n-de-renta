"""Informe ejecutivo del borrador de renta del comerciante (PN), en PDF con la
marca Tributando (azul marino + dorado). Incluye la actividad comercial (CMV,
depreciación), el patrimonio, los activos fijos y la guía de conciliación fiscal
del formato 2517 (anexo del 210).
"""
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .modelos import DatosDeclaracion, Liquidacion, ResultadoExogena

AZUL = colors.HexColor("#1E2432")
ORO = colors.HexColor("#C9A75A")
ORO_CLARO = colors.HexColor("#E0C584")
GRIS = colors.HexColor("#f6f4ee")
ROJO = colors.HexColor("#b3372f")
VERDE = colors.HexColor("#1f9d55")
GRIS_TXT = colors.HexColor("#5a6b7f")


def _fmt(v) -> str:
    return f"${round(v or 0):,.0f}".replace(",", ".")


def _estilos():
    base = getSampleStyleSheet()
    return {
        "n": base["Normal"],
        "titulo": ParagraphStyle("t", parent=base["Title"], fontSize=16,
                                 textColor=colors.white, spaceAfter=0, leading=19),
        "marca": ParagraphStyle("m", parent=base["Normal"], fontSize=9,
                                textColor=ORO_CLARO, spaceAfter=0),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5,
                             textColor=AZUL, spaceBefore=10, spaceAfter=4),
        "cel": ParagraphStyle("c", parent=base["Normal"], fontSize=8.5, leading=11),
        "celr": ParagraphStyle("cr", parent=base["Normal"], fontSize=8.5,
                               alignment=TA_RIGHT, leading=11),
        "nota": ParagraphStyle("no", parent=base["Normal"], fontSize=7.8,
                               textColor=GRIS_TXT, leading=10.5),
        "kpi_v": ParagraphStyle("kv", parent=base["Normal"], fontSize=13,
                                alignment=TA_CENTER, textColor=AZUL, leading=15),
        "kpi_e": ParagraphStyle("ke", parent=base["Normal"], fontSize=7,
                                alignment=TA_CENTER, textColor=GRIS_TXT, leading=9),
    }


def _tabla(data, anchos, header=True, total_rows=()):
    est = [
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#e2dccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if header:
        est += [("BACKGROUND", (0, 0), (-1, 0), AZUL),
                ("TEXTCOLOR", (0, 0), (-1, 0), ORO_CLARO),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    for r in total_rows:
        est += [("BACKGROUND", (0, r), (-1, r), GRIS),
                ("FONTNAME", (0, r), (-1, r), "Helvetica-Bold")]
    t = Table(data, colWidths=anchos)
    t.setStyle(TableStyle(est))
    return t


def generar_informe_comerciante_pdf(salida: Path, datos: DatosDeclaracion,
                                    liq: Liquidacion,
                                    exogena: Optional[ResultadoExogena] = None) -> Path:
    salida = Path(salida)
    S = _estilos()
    R = lambda n: liq.renglones.get(n, 0)
    doc = SimpleDocTemplate(str(salida), pagesize=letter,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            leftMargin=15 * mm, rightMargin=15 * mm)
    el = []

    # --- encabezado con marca ---
    con = datos.contribuyente
    nombre = " ".join(x for x in (con.primer_nombre, con.otros_nombres,
                                  con.primer_apellido, con.segundo_apellido) if x).strip()
    cab = Table([[Paragraph("Tributando<font color='#C9A75A'>.co</font>", S["titulo"]),
                  Paragraph(f"Informe borrador — Comerciante (PN)<br/>"
                            f"<font size=8>{nombre or ''} · NIT {con.nit or '—'} · "
                            f"Año gravable 2025</font>", S["marca"])]],
                 colWidths=[70 * mm, 105 * mm])
    cab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), AZUL),
                             ("TOPPADDING", (0, 0), (-1, -1), 10),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                             ("LEFTPADDING", (0, 0), (-1, -1), 12),
                             ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LINEBELOW", (0, 0), (-1, -1), 3, ORO),
                             ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    el += [cab, Spacer(1, 8)]

    # --- KPIs ---
    pagar, favor = R(136), R(137)
    res_txt = _fmt(pagar) if pagar >= favor else _fmt(favor)
    res_lbl = "SALDO A PAGAR" if pagar >= favor else "SALDO A FAVOR"
    kpis = [[Paragraph(_fmt(R(97) + R(103)), S["kpi_v"]),
             Paragraph(_fmt(R(129)), S["kpi_v"]),
             Paragraph(_fmt(R(133)), S["kpi_v"]),
             Paragraph(f"<font color='{'#b3372f' if pagar>=favor else '#1f9d55'}'>"
                       f"{res_txt}</font>", S["kpi_v"])],
            [Paragraph("Renta líq. gravable", S["kpi_e"]),
             Paragraph("Impuesto a cargo", S["kpi_e"]),
             Paragraph("Anticipo 2026", S["kpi_e"]),
             Paragraph(res_lbl, S["kpi_e"])]]
    tk = Table(kpis, colWidths=[43 * mm] * 4)
    tk.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, ORO),
                            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2dccc")),
                            ("BACKGROUND", (0, 0), (-1, -1), GRIS),
                            ("TOPPADDING", (0, 0), (-1, -1), 6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [tk, Spacer(1, 6)]

    # --- actividad comercial (rentas no laborales) ---
    el += [Paragraph("Actividad comercial — Cédula de rentas no laborales", S["h2"])]
    cmv = max(0, datos.compras_mercancia + datos.inventario_inicial - datos.inventario_final)
    from .motor_calculo import calcular_depreciacion
    dep = calcular_depreciacion(datos)
    otros = datos.no_laboral.costos_deducciones
    filas = [["Concepto", "Valor", "Renglón 210"],
             ["Ingresos brutos (ventas)", _fmt(R(74)), "R74"],
             ["(−) Costo de ventas (CMV)", _fmt(cmv), "R77"],
             ["(−) Otros costos/gastos", _fmt(otros), "R77"],
             ["(−) Depreciación (Art. 137)", _fmt(dep), "R77"],
             ["= Renta líquida no laboral", _fmt(R(78)), "R78"]]
    el += [_tabla(filas, [95 * mm, 45 * mm, 35 * mm], total_rows=(5,)), Spacer(1, 4)]

    # CMV desglosado
    el += [Paragraph("Costo de la mercancía vendida (Arts. 62/63)", S["h2"])]
    fc = [["Inventario inicial", "(+) Compras", "(−) Inventario final", "= CMV"],
          [_fmt(datos.inventario_inicial), _fmt(datos.compras_mercancia),
           _fmt(datos.inventario_final), _fmt(cmv)]]
    el += [_tabla(fc, [43 * mm] * 4), Spacer(1, 6)]

    # --- patrimonio ---
    el += [Paragraph("Patrimonio", S["h2"])]
    fp = [["Concepto", "Valor", "Renglón"],
          ["Patrimonio bruto", _fmt(R(29)), "R29"],
          ["(−) Deudas", _fmt(R(30)), "R30"],
          ["= Patrimonio líquido", _fmt(R(31)), "R31"]]
    el += [_tabla(fp, [95 * mm, 45 * mm, 35 * mm], total_rows=(3,)), Spacer(1, 6)]

    # --- activos fijos ---
    if datos.activos_fijos:
        el += [Paragraph("Activos fijos", S["h2"])]
        fa = [["Descripción", "Categoría", "Valor", "¿Deprecia?"]]
        for a in datos.activos_fijos:
            fa.append([Paragraph(a.descripcion or a.categoria, S["cel"]), a.categoria,
                       _fmt(a.valor), "No" if a.categoria == "no_deprecia" else "Sí"])
        el += [_tabla(fa, [75 * mm, 35 * mm, 35 * mm, 30 * mm]), Spacer(1, 6)]

    # --- conciliación fiscal 2517 ---
    el += [Paragraph("Guía de conciliación fiscal — Formato 2517 (anexo 210)", S["h2"])]
    g = [["Valor fiscal", "Va en el formato 2517 (hoja H3 · fila · concepto)"],
         [_fmt(R(74)), "fila 40 · Venta de bienes (territorio nacional)"],
         [_fmt(datos.inventario_inicial), "fila 150 · CMV comerciantes: Inventario inicial"],
         [_fmt(datos.compras_mercancia), "fila 151 · CMV comerciantes: compras locales"],
         [_fmt(datos.inventario_final), "fila 153 · CMV comerciantes: Inventario final"],
         [_fmt(dep), "fila 167 · Depreciación PP&E (del costo)"]]
    el += [_tabla(g, [40 * mm, 135 * mm]),
           Paragraph("El formato 2517 pide además la columna CONTABLE (NIIF): esta guía "
                     "entrega la base fiscal ya clasificada por cédula. Los activos van a "
                     "la hoja H6 (Activos fijos) por categoría.", S["nota"]), Spacer(1, 6)]

    # --- observaciones ---
    if liq.advertencias:
        el += [Paragraph("Observaciones para el contador", S["h2"])]
        for a in liq.advertencias:
            el += [Paragraph("• " + a, S["nota"]), Spacer(1, 2)]

    el += [Spacer(1, 8), HRFlowable(width="100%", color=ORO, thickness=1),
           Paragraph("Borrador de apoyo — no reemplaza la asesoría profesional ni la "
                     "declaración oficial. Tributando.co", S["nota"])]

    doc.build(el)
    return salida
