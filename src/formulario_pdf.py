"""Formulario 210 en PDF con el layout del formulario oficial de la DIAN.

Réplica visual del formato (casillas numeradas 24–141, secciones y columnas
de la cédula general) para revisión y entrega al cliente. NO es el formulario
oficial: no lleva código de barras ni número de formulario y va marcado como
BORRADOR, porque la declaración real se diligencia en el MUII de la DIAN.

Los valores se aproximan al múltiplo de mil más cercano, como exige el
formulario oficial.
"""
from datetime import date
from pathlib import Path
from typing import Optional

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .modelos import DatosDeclaracion, Liquidacion
from .parametros import Parametros

AZUL = HexColor("#27506e")
FONDO = HexColor("#dce7f0")
GRIS = HexColor("#8fa6b8")

ML, MR, MT = 20, 20, 24          # márgenes
W, H = letter                    # 612 × 792


def _mil(v: float) -> str:
    return f"{round(v / 1000) * 1000:,.0f}"


class _F210Canvas:
    """Ayudas de dibujo sobre el canvas."""

    def __init__(self, c: canvas.Canvas):
        self.c = c

    def caja(self, x, y, w, h, relleno=None):
        self.c.setStrokeColor(GRIS)
        self.c.setLineWidth(0.5)
        if relleno:
            self.c.setFillColor(relleno)
            self.c.rect(x, y, w, h, stroke=1, fill=1)
        else:
            self.c.rect(x, y, w, h, stroke=1, fill=0)
        self.c.setFillColor(black)

    def etiqueta(self, x, y, texto, tam=5.6, bold=False, color=black, ancho=None):
        self.c.setFillColor(color)
        self.c.setFont("Helvetica-Bold" if bold else "Helvetica", tam)
        if ancho:  # recorte manual
            while self.c.stringWidth(texto) > ancho and len(texto) > 4:
                texto = texto[:-1]
        self.c.drawString(x, y, texto)
        self.c.setFillColor(black)

    def casilla(self, x, y, w, h, num, valor, tam=6.6):
        """Celda oficial: numerito en recuadro azul + valor a la derecha."""
        self.caja(x, y, w, h)
        nb = 14
        self.caja(x, y, nb, h, relleno=FONDO)
        self.c.setFont("Helvetica-Bold", 5.4)
        self.c.setFillColor(AZUL)
        self.c.drawCentredString(x + nb / 2, y + h / 2 - 2, str(num))
        self.c.setFillColor(black)
        self.c.setFont("Helvetica", tam)
        self.c.drawRightString(x + w - 3, y + h / 2 - 2.2, valor)

    def seccion_vertical(self, x, y, h, texto):
        self.caja(x, y, 13, h, relleno=FONDO)
        self.c.saveState()
        self.c.translate(x + 8.5, y + h / 2)
        self.c.rotate(90)
        self.c.setFont("Helvetica-Bold", 5.6)
        self.c.setFillColor(AZUL)
        self.c.drawCentredString(0, 0, texto)
        self.c.restoreState()
        self.c.setFillColor(black)


def generar_formulario_pdf(
    ruta: Path,
    datos: DatosDeclaracion,
    liq: Liquidacion,
    p: Parametros,
) -> Path:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(ruta), pagesize=letter)
    f = _F210Canvas(c)
    R = liq.r
    con = datos.contribuyente

    # ================= marca de agua =================
    c.saveState()
    c.translate(W / 2, H / 2)
    c.rotate(45)
    c.setFont("Helvetica-Bold", 64)
    c.setFillColor(HexColor("#d8dee6"))
    c.drawCentredString(0, 0, "BORRADOR")
    c.restoreState()
    c.setFillColor(black)

    # ================= encabezado =================
    y = H - MT
    f.caja(ML, y - 40, W - ML - MR, 40)
    f.etiqueta(ML + 4, y - 10, "Declaración de Renta y Complementario Personas Naturales"
               " y Asimiladas Residentes — Formulario 210", 8.5, bold=True, color=AZUL)
    f.etiqueta(ML + 4, y - 21, f"Año gravable {p.anio_gravable} · UVT ${p.uvt:,.0f} · "
               f"Generado {date.today().strftime('%d/%m/%Y')}", 6.5)
    f.etiqueta(ML + 4, y - 33, "BORRADOR DE TRABAJO — No válido para presentación ante la DIAN. "
               "Espacios oficiales (código de barras, No. de formulario) no aplican.", 6.2,
               bold=True, color=HexColor("#b3372f"))
    y -= 46

    # ---- fila datos del declarante ----
    alto = 22
    campos = [
        ("5. Número de Identificación Tributaria (NIT)", 120, " ".join(con.nit or "")),
        ("6. DV", 26, con.dv),
        ("7. Primer apellido", 100, con.primer_apellido),
        ("8. Segundo apellido", 100, con.segundo_apellido),
        ("9. Primer nombre", 96, con.primer_nombre),
        ("10. Otros nombres", 130, con.otros_nombres),
    ]
    x = ML
    for titulo, w, valor in campos:
        f.caja(x, y - alto, w, alto)
        f.etiqueta(x + 2, y - 7, titulo, 5.2, color=AZUL, ancho=w - 4)
        f.etiqueta(x + 3, y - 17.5, str(valor or ""), 7.5, bold=True)
        x += w
    y -= alto

    campos2 = [
        ("24. Actividad económica", 78, con.actividad_economica),
        ("25. Cód. corrección", 60, con.codigo_correccion if con.es_correccion else ""),
        ("26. No. formulario anterior", 100, con.formulario_anterior),
        ("27. Fracción año gravable siguiente", 110, ""),
        ("28. 1% compras con factura electrónica", 224, _mil(R(28))),
    ]
    x = ML
    for titulo, w, valor in campos2:
        f.caja(x, y - alto, w, alto)
        f.etiqueta(x + 2, y - 7, titulo, 5.2, color=AZUL, ancho=w - 4)
        f.etiqueta(x + 3, y - 17.5, str(valor or ""), 7.5, bold=True)
        x += w
    y -= alto + 3

    # ---- patrimonio ----
    alto = 15
    f.caja(ML, y - alto, 62, alto, relleno=FONDO)
    f.etiqueta(ML + 3, y - 9.5, "Patrimonio", 6.4, bold=True, color=AZUL)
    tercio = (W - ML - MR - 62) / 3
    f.etiqueta(ML + 66, y - 9.5, "Total patrimonio bruto", 5.6)
    f.casilla(ML + 62 + tercio - 108, y - alto, 108, alto, 29, _mil(R(29)))
    f.etiqueta(ML + 62 + tercio + 4, y - 9.5, "Deudas", 5.6)
    f.casilla(ML + 62 + 2 * tercio - 108, y - alto, 108, alto, 30, _mil(R(30)))
    f.etiqueta(ML + 62 + 2 * tercio + 4, y - 9.5, "Total patrimonio líquido", 5.6)
    f.casilla(W - MR - 108, y - alto, 108, alto, 31, _mil(R(31)))
    y -= alto + 3

    # ================= cédula general =================
    col_label = 128
    ancho_col = (W - ML - MR - 13 - col_label) / 4
    fila_h = 12.6

    # (etiqueta, casillas por columna [trabajo, honorarios, capital, no laboral])
    filas = [
        ("Ingresos brutos", [32, 43, 58, 74]),
        ("Devoluciones, rebajas y descuentos", [None, None, None, 75]),
        ("Ingresos no constitutivos de renta", [33, 44, 59, 76]),
        ("Costos y deducciones procedentes", [None, 45, 60, 77]),
        ("Renta líquida", [34, 46, 61, 78]),
        ("Rentas líquidas pasivas - ECE", [None, None, 62, 79]),
        ("Aportes voluntarios AFC, FVP y/o AVC", [35, 47, 63, 80]),
        ("Otras rentas exentas", [36, 48, 64, 81]),
        ("Total rentas exentas", [37, 49, 65, 82]),
        ("Intereses de vivienda", [38, 50, 66, 83]),
        ("Otras deducciones imputables", [39, 51, 67, 84]),
        ("Total deducciones imputables", [40, 52, 68, 85]),
        ("Rentas exentas y/o ded. imputables (limitadas)", [41, 53, 69, 86]),
        ("Renta líquida ordinaria del ejercicio", [None, 54, 70, 87]),
        ("Pérdida líquida del ejercicio", [None, 55, 71, 88]),
        ("Compensaciones por pérdidas", [None, 56, 72, 89]),
        ("Renta líquida ordinaria", [42, 57, 73, 90]),
    ]
    encabezados = ["Rentas de trabajo", "Rentas de trabajo sin relación laboral",
                   "Rentas de capital", "Rentas no laborales"]

    alto_grid = fila_h * len(filas) + 13
    f.seccion_vertical(ML, y - alto_grid, alto_grid, "Cédula general")
    x0 = ML + 13
    # encabezados de columnas
    f.caja(x0, y - 13, col_label, 13, relleno=FONDO)
    f.etiqueta(x0 + 2, y - 9, "Conceptos / renta", 5.6, bold=True, color=AZUL)
    for i, enc in enumerate(encabezados):
        cx = x0 + col_label + i * ancho_col
        f.caja(cx, y - 13, ancho_col, 13, relleno=FONDO)
        f.etiqueta(cx + 2, y - 9, enc, 5.4, bold=True, color=AZUL, ancho=ancho_col - 4)
    yy = y - 13
    for etiqueta, nums in filas:
        yy -= fila_h
        f.caja(x0, yy, col_label, fila_h)
        f.etiqueta(x0 + 2, yy + 3.6, etiqueta, 5.3, ancho=col_label - 4)
        for i, num in enumerate(nums):
            cx = x0 + col_label + i * ancho_col
            if num is None:
                f.caja(cx, yy, ancho_col, fila_h, relleno=HexColor("#eef2f6"))
            else:
                f.casilla(cx, yy, ancho_col, fila_h, num, _mil(R(num)), tam=6.2)
    y = yy - 3

    # ---- totales cédula general (91-98) ----
    alto = 14
    mitades = (W - ML - MR) / 4
    pares = [("Renta líquida cédula general", 91), ("Rentas exentas y ded. limitadas", 92),
             ("Renta líquida ordinaria céd. gen.", 93), ("Comp. pérdidas 2018 y ant.", 94)]
    pares2 = [("Comp. exceso renta presuntiva", 95), ("Rentas gravables", 96),
              ("Renta líquida gravable céd. gen.", 97), ("Renta presuntiva", 98)]
    for fila_pares in (pares, pares2):
        x = ML
        for texto, num in fila_pares:
            f.caja(x, y - alto, mitades, alto)
            f.etiqueta(x + 2, y - 6.5, texto, 5.0, color=AZUL, ancho=mitades - 4)
            f.casilla(x + mitades - 88, y - alto + 1, 86, alto - 2, num, _mil(R(num)), tam=6.2)
            x += mitades
        y -= alto
    y -= 3

    # ================= bloques inferiores =================
    mitad = (W - ML - MR) / 2
    fila_h2 = 12.6

    def bloque(x, y0, titulo, items):
        n = len(items)
        alto_b = fila_h2 * n + 11
        f.seccion_vertical(x, y0 - alto_b, alto_b, titulo)
        f.caja(x + 13, y0 - 11, mitad - 13, 11, relleno=FONDO)
        yy = y0 - 11
        for texto, num in items:
            yy -= fila_h2
            f.caja(x + 13, yy, mitad - 13 - 96, fila_h2)
            f.etiqueta(x + 15, yy + 3.6, texto, 5.2, ancho=mitad - 13 - 100)
            f.casilla(x + mitad - 96, yy, 96, fila_h2, num, _mil(R(num)), tam=6.2)
        return y0 - alto_b

    y_izq = bloque(ML, y, "Céd. pensiones", [
        ("Ingresos brutos por pensiones del país y del exterior", 99),
        ("Ingresos no constitutivos de renta", 100),
        ("Renta líquida", 101),
        ("Rentas exentas de pensiones", 102),
        ("Renta líquida gravable céd. de pensiones", 103),
    ]) - 3
    y_izq = bloque(ML, y_izq, "Céd. dividendos", [
        ("Dividendos y participaciones 2016 y anteriores", 104),
        ("Ingresos no constitutivos de renta", 105),
        ("Renta líquida ordinaria año 2016 y anteriores", 106),
        ("1a. subcédula año 2017 y sig. num. 3 art. 49", 107),
        ("2a. subcédula año 2017 y sig. par. 2 art. 49", 108),
        ("Dividendos y participaciones del exterior", 109),
        ("Rentas exentas casilla 109", 110),
    ]) - 3
    # renta líquida gravable total (111)
    f.caja(ML, y_izq - 14, mitad - 96, 14)
    f.etiqueta(ML + 2, y_izq - 8.5, "Renta líquida gravable (cédula general, pensiones y "
               "dividendos 2017+)", 5.0, ancho=mitad - 100)
    f.casilla(ML + mitad - 96, y_izq - 14, 96, 14, 111, _mil(R(111)), tam=6.4)
    y_izq -= 17
    y_izq = bloque(ML, y_izq, "Ganancias ocas.", [
        ("Ingresos por ganancias ocasionales país y exterior", 112),
        ("Costos por ganancias ocasionales", 113),
        ("Ganancias ocasionales no gravadas y exentas", 114),
        ("Ganancias ocasionales gravables", 115),
    ])

    y_der = bloque(ML + mitad, y, "Liquidación privada", [
        ("Imp. cédulas general, pensiones y dividendos", 116),
        ("Imp. renta presuntiva, pensiones y dividendos", 117),
        ("Por dividendos 2017 y sig., 1a. subcédula", 118),
        ("Por dividendos y participaciones 2016", 119),
        ("Por dividendos recibidos del exterior", 120),
        ("Total impuesto sobre las rentas líquidas gravables", 121),
        ("Desc. impuestos pagados en el exterior", 122),
        ("Descuento por donaciones", 123),
        ("Desc. dividendos, participaciones y otros", 124),
        ("Total descuentos tributarios", 125),
        ("Impuesto neto de renta", 126),
        ("Impuesto de ganancias ocasionales", 127),
        ("Desc. imp. exterior por ganancias ocasionales", 128),
        ("Total impuesto a cargo", 129),
        ("Anticipo renta liquidado año gravable anterior", 130),
        ("Saldo a favor año anterior sin devolución", 131),
        ("Retenciones año gravable a declarar", 132),
        ("Anticipo renta para el año gravable siguiente", 133),
    ])

    y = min(y_izq, y_der) - 5

    # ---- saldos y dependientes ----
    alto = 15
    cuartos = (W - ML - MR) / 4
    for i, (texto, num) in enumerate([("Saldo a pagar por impuesto", 134),
                                      ("Sanciones", 135),
                                      ("Total saldo a pagar", 136),
                                      ("Total saldo a favor", 137)]):
        x = ML + i * cuartos
        f.caja(x, y - alto, cuartos, alto)
        f.etiqueta(x + 2, y - 6.5, texto, 5.2, bold=True, color=AZUL, ancho=cuartos - 4)
        f.casilla(x + cuartos - 90, y - alto + 1, 88, alto - 2, num, _mil(R(num)), tam=6.6)
    y -= alto
    for i, (texto, num, val) in enumerate([
            ("Número de dependientes económicos", 138, f"{datos.dependientes}"),
            ("Adición por dependientes a la casilla 92", 139, _mil(R(139))),
            ("Superó tope indicativo art. 336-1 E.T.", 140, ""),
            ("Aporte voluntario", 141, _mil(R(141)))]):
        x = ML + i * cuartos
        f.caja(x, y - alto, cuartos, alto)
        f.etiqueta(x + 2, y - 6.5, texto, 5.2, color=AZUL, ancho=cuartos - 4)
        f.casilla(x + cuartos - 90, y - alto + 1, 88, alto - 2, num, val, tam=6.6)
    y -= alto + 6

    # ---- pie de firmas ----
    f.caja(ML, y - 34, (W - ML - MR) / 2, 34)
    f.etiqueta(ML + 3, y - 8, "Firma del declarante o de quien lo representa", 5.4, color=AZUL)
    f.caja(ML + (W - ML - MR) / 2, y - 34, (W - ML - MR) / 2, 34)
    f.etiqueta(ML + (W - ML - MR) / 2 + 3, y - 8, "Firma contador · 994. Con salvedades",
               5.4, color=AZUL)
    y -= 40
    f.etiqueta(ML, y, "Documento de trabajo generado localmente a partir de la información "
               "exógena y los datos del contribuyente. Verifique la normativa vigente con su "
               "contador antes de diligenciar el formulario oficial en el portal DIAN.", 5.8)

    c.showPage()
    c.save()
    return ruta
