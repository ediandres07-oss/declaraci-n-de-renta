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
AZUL210 = HexColor("#46759e")        # caja del "210" del encabezado oficial
FONDO = HexColor("#dce7f0")
GRIS = HexColor("#8fa6b8")
GRIS_BLOQ = HexColor("#d9dfe7")      # celdas no diligenciables (como el oficial)

ML, MR, MT = 20, 20, 24          # márgenes
W, H = letter                    # 612 × 792


def _mil(v: float) -> str:
    return f"{round(v / 1000) * 1000:,.0f}"


class _F210Canvas:
    """Ayudas de dibujo sobre el canvas."""

    def __init__(self, c: canvas.Canvas, rellenable: bool = True):
        self.c = c
        self.rellenable = rellenable
        self._campos = set()      # renglones ya registrados como campo de formulario

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
        """Celda oficial: numerito en recuadro azul + valor a la derecha.

        En modo rellenable el valor lo pinta un campo AcroForm editable, no
        texto estático: así el usuario puede corregir el borrador en su lector
        de PDF. El campo se alinea a la derecha en `_alinear_campos_derecha`.
        """
        self.caja(x, y, w, h)
        nb = 14
        self.caja(x, y, nb, h, relleno=FONDO)
        self.c.setFont("Helvetica-Bold", 5.4)
        self.c.setFillColor(AZUL)
        self.c.drawCentredString(x + nb / 2, y + h / 2 - 2, str(num))
        self.c.setFillColor(black)

        if self.rellenable and num not in self._campos:
            self._campos.add(num)
            self.c.acroForm.textfield(
                name=f"R{num}", value=valor, tooltip=f"Renglón {num}",
                x=x + nb + 1, y=y + 1, width=w - nb - 3, height=h - 2,
                fontName="Helvetica", fontSize=tam, textColor=black,
                fillColor=None, borderColor=None, borderWidth=0,
                maxlen=24, annotationFlags="print",
            )
        else:
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


def _alinear_campos_derecha(ruta: Path) -> None:
    """Marca los campos como alineados a la derecha (/Q 2), como el formulario oficial.

    reportlab no expone la alineación al crear el campo y escribe la apariencia
    alineada a la izquierda. Se fija `/Q 2` en cada anotación y `/NeedAppearances`
    en el AcroForm, para que el lector regenere la apariencia respetándola. En
    lectores que no regeneran apariencias el valor sigue siendo correcto, solo
    queda alineado a la izquierda.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import BooleanObject, NameObject, NumberObject

    lector = PdfReader(str(ruta))
    escritor = PdfWriter()
    escritor.append(lector)

    for pagina in escritor.pages:
        for anot in pagina.get("/Annots", []) or []:
            obj = anot.get_object()
            if obj.get("/FT") == "/Tx":
                obj[NameObject("/Q")] = NumberObject(2)

    raiz = escritor._root_object
    if "/AcroForm" in raiz:
        raiz["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)

    with open(ruta, "wb") as fh:
        escritor.write(fh)


def generar_formulario_pdf(
    ruta: Path,
    datos: DatosDeclaracion,
    liq: Liquidacion,
    p: Parametros,
    rellenable: bool = True,
    marca: str = "BORRADOR",
) -> Path:
    """`marca` es el texto de la marca de agua. Con "BORRADOR" (defecto) se
    imprime una sola diagonal tenue. Con cualquier otro valor (ej. "MUESTRA")
    se tejen varias diagonales más visibles: así una muestra gratis para
    contadores no puede pasar como declaración real."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(ruta), pagesize=letter)
    f = _F210Canvas(c, rellenable=rellenable)
    R = liq.r
    con = datos.contribuyente

    # ================= marca de agua =================
    if marca == "BORRADOR":
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(45)
        c.setFont("Helvetica-Bold", 64)
        c.setFillColor(HexColor("#d8dee6"))
        c.drawCentredString(0, 0, "BORRADOR")
        c.restoreState()
    else:
        # marca de muestra: tres diagonales tenues, sin invadir la lectura
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(45)
        c.setFont("Helvetica-Bold", 42)
        c.setFillColor(HexColor("#efe1cb"))          # dorado muy suave
        for fila in (-1, 0, 1):
            c.drawCentredString(0, fila * 260, marca)
        c.restoreState()
    c.setFillColor(black)

    # ================= encabezado (layout oficial) =================
    y = H - MT
    h1 = 36
    w_logo, w_blank, w_210 = 150, 70, 86
    w_titulo = W - ML - MR - w_logo - w_blank - w_210
    # logotipo (trazo aproximado al oficial)
    f.caja(ML, y - h1, w_logo, h1)
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(HexColor("#6b7680"))
    c.drawCentredString(ML + w_logo / 2, y - h1 / 2 - 8, "D I A N")
    c.setFillColor(black)
    # título central en tres líneas, como el formulario oficial
    f.caja(ML + w_logo, y - h1, w_titulo, h1)
    cx_t = ML + w_logo + w_titulo / 2
    c.setFont("Helvetica-Bold", 8.6)
    c.drawCentredString(cx_t, y - 11, "Declaración de renta y complementario")
    c.drawCentredString(cx_t, y - 21, "personas naturales y asimiladas residentes")
    c.drawCentredString(cx_t, y - 31, "y sucesiones ilíquidas de causantes residentes")
    # caja en blanco y gran "210" azul
    f.caja(ML + w_logo + w_titulo, y - h1, w_blank, h1)
    f.caja(W - MR - w_210, y - h1, w_210, h1, relleno=AZUL210)
    c.setFont("Helvetica-Bold", 27)
    c.setFillColor(white)
    c.drawCentredString(W - MR - w_210 / 2, y - h1 / 2 - 9, "210")
    c.setFillColor(black)
    y -= h1

    # ---- 1. Año (cajitas de dígitos) ----
    h_a = 13
    f.etiqueta(ML + 2, y - 9, "1. Año", 6.0)
    for i, digito in enumerate(str(p.anio_gravable)):
        bx = ML + 32 + i * 15
        f.caja(bx, y - h_a + 1, 13, h_a - 2)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawCentredString(bx + 6.5, y - h_a / 2 - 3, digito)
    y -= h_a

    # ---- espacio reservado para la DIAN + 4. número de formulario ----
    h_r = 34
    w_res = (W - ML - MR) * 0.55
    f.caja(ML, y - h_r, w_res, h_r)
    f.etiqueta(ML + 3, y - 8, "Espacio reservado para la DIAN", 5.4)
    f.etiqueta(ML + 3, y - 18, "BORRADOR DE TRABAJO — No válido para presentación ante la DIAN. "
               "Espacios oficiales no aplican.", 5.8, bold=True, color=HexColor("#b3372f"),
               ancho=w_res - 6)
    f.etiqueta(ML + 3, y - 27, f"Año gravable {p.anio_gravable} · UVT ${p.uvt:,.0f} · "
               f"Generado {date.today().strftime('%d/%m/%Y')}", 5.6)
    f.caja(ML + w_res, y - h_r, W - ML - MR - w_res, h_r)
    f.etiqueta(ML + w_res + 3, y - 8, "4. Número de formulario", 5.4)
    y -= h_r

    # ---- fila datos del declarante (con casilla 12, como el oficial) ----
    alto = 22
    campos = [
        ("5. Número de Identificación Tributaria (NIT)", 108, " ".join(con.nit or "")),
        ("6. DV", 24, con.dv),
        ("7. Primer apellido", 96, con.primer_apellido),
        ("8. Segundo apellido", 96, con.segundo_apellido),
        ("9. Primer nombre", 88, con.primer_nombre),
        ("10. Otros nombres", 102, con.otros_nombres),
        ("12. Cód. Dirección seccional", 58, ""),
    ]
    x = ML
    for titulo, w, valor in campos:
        f.caja(x, y - alto, w, alto)
        f.etiqueta(x + 2, y - 7, titulo, 5.0, color=AZUL, ancho=w - 4)
        f.etiqueta(x + 3, y - 17.5, str(valor or ""), 7.5, bold=True)
        x += w
    # rótulo vertical "Datos del declarante" en el margen, como el oficial
    c.saveState()
    c.translate(ML - 6, y - alto)
    c.rotate(90)
    c.setFont("Helvetica", 4.6)
    c.setFillColor(GRIS)
    c.drawString(0, 0, "Datos del declarante")
    c.restoreState()
    c.setFillColor(black)
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
    fila_h = 11.8

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
                f.caja(cx, yy, ancho_col, fila_h, relleno=GRIS_BLOQ)
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
            f.etiqueta(x + 2, y - 6.5, texto, 5.0, color=AZUL, ancho=mitades - 92)
            f.casilla(x + mitades - 88, y - alto + 1, 86, alto - 2, num, _mil(R(num)), tam=6.2)
            x += mitades
        y -= alto
    y -= 3

    # ================= bloques inferiores =================
    mitad = (W - ML - MR) / 2
    fila_h2 = 11.8

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
        f.etiqueta(x + 2, y - 6.5, texto, 5.2, bold=True, color=AZUL, ancho=cuartos - 94)
        f.casilla(x + cuartos - 90, y - alto + 1, 88, alto - 2, num, _mil(R(num)), tam=6.6)
    y -= alto
    for i, (texto, num, val) in enumerate([
            ("Número de dependientes económicos", 138, f"{datos.dependientes}"),
            ("Adición por dependientes a la casilla 92", 139, _mil(R(139))),
            ("Superó tope indicativo art. 336-1 E.T.", 140, ""),
            ("Aporte voluntario", 141, _mil(R(141)))]):
        x = ML + i * cuartos
        f.caja(x, y - alto, cuartos, alto)
        f.etiqueta(x + 2, y - 6.5, texto, 5.2, color=AZUL, ancho=cuartos - 94)
        f.casilla(x + cuartos - 90, y - alto + 1, 88, alto - 2, num, val, tam=6.6)
    y -= alto + 6

    # ---- pie de firmas (layout oficial: 981/982/983/994/997/980/996) ----
    mitad2 = (W - ML - MR) / 2
    h_f = 13
    # izquierda: representación, firma declarante, contador, tarjeta
    f.caja(ML, y - h_f, 92, h_f)
    f.etiqueta(ML + 2, y - 6, "981. Cód. Representación", 4.8)
    f.caja(ML, y - 2 * h_f, 92, h_f)
    f.etiqueta(ML + 2, y - h_f - 6, "982. Cód. Contador", 4.8)
    f.caja(ML, y - 3 * h_f, 92, h_f)
    f.etiqueta(ML + 2, y - 2 * h_f - 6, "983. No. Tarjeta profesional", 4.8)
    f.caja(ML + 92, y - 2 * h_f, mitad2 - 92, 2 * h_f)
    f.etiqueta(ML + 95, y - 6, "Firma del declarante o de quien lo representa", 5.0)
    f.caja(ML + 92, y - 3 * h_f, mitad2 - 92 - 78, h_f)
    f.etiqueta(ML + 95, y - 2 * h_f - 6, "Firma contador", 5.0)
    f.caja(ML + mitad2 - 78, y - 3 * h_f, 78, h_f)
    f.etiqueta(ML + mitad2 - 75, y - 2 * h_f - 6, "994. Con salvedades", 4.8)
    # derecha: sello recaudadora, pago total, número interno
    f.caja(ML + mitad2, y - 2 * h_f, mitad2, 2 * h_f)
    f.etiqueta(ML + mitad2 + 3, y - 6,
               "997. Espacio exclusivo para el sello de la entidad recaudadora", 5.0)
    f.caja(ML + mitad2, y - 3 * h_f, mitad2 / 2, h_f)
    f.etiqueta(ML + mitad2 + 3, y - 2 * h_f - 6, "980. Pago total $", 5.2, bold=True)
    f.caja(ML + mitad2 + mitad2 / 2, y - 3 * h_f, mitad2 / 2, h_f)
    f.etiqueta(ML + mitad2 + mitad2 / 2 + 3, y - 2 * h_f - 6,
               "996. Espacio para el número interno de la DIAN / Adhesivo", 4.6,
               color=AZUL210, ancho=mitad2 / 2 - 6)
    y -= 3 * h_f + 6
    f.etiqueta(ML, y, "Documento de trabajo generado localmente a partir de la información "
               "exógena y los datos del contribuyente. Verifique la normativa vigente con su "
               "contador antes de diligenciar el formulario oficial en el portal DIAN.", 5.8)

    c.showPage()
    c.save()
    if rellenable:
        _alinear_campos_derecha(ruta)
    return ruta


def sellar_formulario_pdf(ruta: Path) -> str:
    """Estampa el código de verificación del documento y lo devuelve.

    Se hace en una segunda pasada porque el código depende del contenido ya
    escrito. El PDF sellado incorpora su propio código, de modo que el sello
    que se recalcule después ya no coincide con el impreso: el código impreso
    identifica el documento, y `src.firma.sello_integridad` sobre el archivo
    final es lo que se compara para detectar alteraciones posteriores.
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as _canvas

    from .firma import codigo_verificacion

    codigo = codigo_verificacion(ruta)

    superposicion = ruta.with_suffix(".sello.pdf")
    c = _canvas.Canvas(str(superposicion), pagesize=letter)
    c.setFont("Helvetica", 5.4)
    c.setFillColor(GRIS)
    c.drawRightString(W - MR, 12, f"Código de verificación: {codigo}")
    c.showPage()
    c.save()

    lector = PdfReader(str(ruta))
    sello = PdfReader(str(superposicion))
    escritor = PdfWriter()
    escritor.append(lector)
    escritor.pages[0].merge_page(sello.pages[0])
    with open(ruta, "wb") as fh:
        escritor.write(fh)
    superposicion.unlink(missing_ok=True)
    return codigo
