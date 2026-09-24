"""Documento privado de constitución de una S.A.S. (Ley 1258 de 2008) en PDF.

Plantilla tomada de la constitución de DISTRIBUIDORA FRITOCOL S.A.S. (radicada en la
Cámara del Magdalena Medio el 14-sep-2026), generalizada a uno o varios accionistas.
`generar_pdf(datos, borrador=True)` devuelve los bytes del PDF; con borrador=True
lleva marca de agua «BORRADOR · TRIBUTANDO.CO».
"""
from __future__ import annotations

import io
from datetime import date

from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

_U = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
      "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho",
      "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés", "veinticuatro",
      "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
_D = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
_C = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos",
      "setecientos", "ochocientos", "novecientos"]
_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


def _cientos(n: int) -> str:
    if n == 100:
        return "cien"
    c, r = divmod(n, 100)
    if r < 30:
        t = _U[r]
    else:
        d, u = divmod(r, 10)
        t = _D[d] + (f" y {_U[u]}" if u else "")
    return " ".join(x for x in (_C[c], t) if x)


def en_letras(n: int) -> str:
    """Número entero a letras en español (hasta billones)."""
    n = int(n)
    if n == 0:
        return "cero"
    partes = []
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    if millones:
        if millones == 1:
            partes.append("un millón")
        else:
            m = en_letras(millones)
            partes.append((m[:-3] + "ún" if m.endswith("uno") else m) + " millones")
    if miles:
        partes.append("mil" if miles == 1 else
                      (lambda s: s[:-3] + "ún" if s.endswith("uno") else s)(_cientos(miles)) + " mil")
    if unidades:
        partes.append(_cientos(unidades))
    return " ".join(partes)


def _num(v: int) -> str:
    return f"{int(v):,}".replace(",", ".")


def _pesos(v: int) -> str:
    return "$" + f"{int(v):,}".replace(",", ".")


def _pesos_letras(v: int) -> str:
    t = en_letras(v).upper()
    return f"{t} DE PESOS ({_pesos(v)})" if t.endswith(("MILLÓN", "MILLONES")) else f"{t} PESOS ({_pesos(v)})"


def _fecha_letras(f: date) -> str:
    return (f"a los {en_letras(f.day)} ({f.day}) días del mes de {_MESES[f.month]} de "
            f"{en_letras(f.year)} ({f.year})")


def _estilos():
    base = dict(fontName="Helvetica", fontSize=10.5, leading=14.5)
    return {
        "tit": ParagraphStyle("tit", fontName="Helvetica-Bold", fontSize=13, leading=17, alignment=TA_CENTER, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName="Helvetica-Bold", fontSize=11.5, leading=15, alignment=TA_CENTER, spaceAfter=12),
        "cap": ParagraphStyle("cap", fontName="Helvetica-Bold", fontSize=10.5, leading=14, spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", alignment=TA_JUSTIFY, spaceAfter=6, **base),
        "c": ParagraphStyle("c", alignment=TA_CENTER, **base),
    }


def _marca(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica-Bold", 46)
    canvas.setFillColorRGB(0.85, 0.85, 0.85)
    canvas.translate(LETTER[0] / 2, LETTER[1] / 2)
    canvas.rotate(45)
    canvas.drawCentredString(0, 0, "BORRADOR · TRIBUTANDO.CO")
    canvas.restoreState()


def generar_pdf(d: dict, borrador: bool = True) -> bytes:
    """d: razon_social, municipio, departamento, direccion, correo, telefono, ciiu,
    objeto, capital (int), valor_nominal (int), fecha (date|None), representante
    (índice del accionista o dict nombre/cedula/expedida), suplente (idem, opcional),
    accionistas: [{nombre, cedula, expedida, domicilio, acciones}]."""
    E = _estilos()
    acc = d["accionistas"]
    uno = len(acc) == 1
    razon = d["razon_social"].strip().upper()
    if not razon.endswith("S.A.S."):
        razon = razon.rstrip(". ").removesuffix(" SAS") + " S.A.S."
    lugar = f"{d['municipio']} ({d['departamento']})"
    f = d.get("fecha") or date.today()
    capital = int(d["capital"])
    vn = int(d.get("valor_nominal") or 1000)
    n_acc = capital // vn

    def persona(a):
        return (f"<b>{a['nombre'].upper()}</b>, mayor de edad, domiciliado(a) en {a.get('domicilio') or lugar}, "
                f"identificado(a) con cédula de ciudadanía número {a['cedula']}"
                + (f" expedida en {a['expedida']}" if a.get("expedida") else "") + ", actuando en nombre propio")

    s = [Paragraph("DOCUMENTO PRIVADO DE CONSTITUCIÓN", E["tit"]),
         Paragraph("SOCIEDAD POR ACCIONES SIMPLIFICADA", E["tit"]),
         Paragraph(razon, E["sub"]),
         Paragraph(f"En el municipio de {lugar}, {_fecha_letras(f)}, "
                   + ("comparece:" if uno else "comparecen:"), E["p"])]
    for a in acc:
        s.append(Paragraph(persona(a) + ".", E["p"]))
    s.append(Paragraph(
        ("Quien manifiesta su voluntad" if uno else "Quienes manifiestan su voluntad")
        + " de constituir una sociedad por acciones simplificada, de naturaleza comercial, regida por "
          "la Ley 1258 de 2008, por las normas que la modifiquen o complementen y, en lo no previsto en "
          "ellas, por las disposiciones aplicables a la sociedad anónima, y por los siguientes estatutos:", E["p"]))

    art = [
        ("CAPÍTULO I — NOMBRE, NATURALEZA, DOMICILIO Y DURACIÓN", None),
        ("Nombre y naturaleza.", f"La sociedad se denomina {razon} Es una sociedad por acciones simplificada, de "
         "naturaleza comercial, con personería jurídica distinta de la de sus accionistas, cuya responsabilidad se "
         "limita al monto de sus respectivos aportes, conforme al artículo 1º de la Ley 1258 de 2008."),
        ("Domicilio.", f"El domicilio principal de la sociedad es el municipio de {lugar}, República de Colombia. "
         f"Su dirección de notificación judicial y comercial es {d['direccion']}. Podrá establecer sucursales, "
         "agencias o establecimientos de comercio en cualquier lugar del territorio nacional o del exterior, por "
         "decisión de la asamblea general de accionistas."),
        ("Duración.", "La sociedad tendrá una duración indefinida, de conformidad con el numeral 4º del artículo 5º "
         "de la Ley 1258 de 2008."),
        ("CAPÍTULO II — OBJETO SOCIAL", None),
        ("Objeto social principal.", f"La sociedad tendrá como objeto social principal {d['objeto'].strip().rstrip('.')}"
         + (f", correspondiente al código CIIU {d['ciiu']}" if d.get("ciiu") else "") + "."),
        ("Actividades conexas.", "En desarrollo de su objeto, y sin perjuicio de lo dispuesto en el inciso segundo del "
         "artículo 5º de la Ley 1258 de 2008, la sociedad podrá realizar cualquier actividad lícita de comercio, y en "
         "especial: adquirir, enajenar, gravar y administrar toda clase de bienes muebles e inmuebles; celebrar "
         "contratos de mutuo, arrendamiento, suministro, mandato, distribución, transporte y cuenta corriente; girar, "
         "aceptar, endosar, avalar, cobrar y pagar títulos valores; celebrar operaciones con entidades del sistema "
         "financiero; constituir garantías reales o personales sobre sus bienes; formar parte de otras sociedades; y "
         "en general ejecutar todo acto o contrato directamente relacionado con su objeto o que tenga como finalidad "
         "ejercer los derechos y cumplir las obligaciones derivadas de su existencia."),
        ("CAPÍTULO III — CAPITAL Y ACCIONES", None),
        ("Capital autorizado.", f"El capital autorizado de la sociedad es de {_pesos_letras(capital)} moneda corriente, "
         f"dividido en {en_letras(n_acc)} ({_num(n_acc)}) acciones ordinarias, nominativas y de igual valor, con un valor "
         f"nominal de {_pesos_letras(vn)} cada una."),
        ("Capital suscrito y pagado.", f"El capital suscrito y pagado de la sociedad es de {_pesos_letras(capital)} "
         f"moneda corriente, representado en {en_letras(n_acc)} ({_num(n_acc)}) acciones ordinarias de valor nominal de "
         f"{_pesos_letras(vn)} cada una, íntegramente suscritas y pagadas en el acto de constitución, conforme a las "
         "disposiciones transitorias del presente documento."),
        ("Clase de acciones y derechos.", "Las acciones son ordinarias y nominativas. Cada acción confiere a su titular "
         "el derecho a un (1) voto en la asamblea general de accionistas, a participar en las utilidades sociales que "
         "se distribuyan y en el remanente de los activos al momento de la liquidación, y a los demás derechos "
         "previstos en la ley y en estos estatutos."),
        ("Libro de registro de accionistas.", "La sociedad llevará un libro de registro de accionistas, en el cual se "
         "inscribirán la titularidad de las acciones, los gravámenes, las limitaciones al dominio y los demás actos "
         "sujetos a inscripción. La sociedad solo reconocerá como accionista a quien figure inscrito en dicho libro."),
        ("Negociación de acciones.", "Las acciones podrán ser negociadas libremente, sin perjuicio del derecho de "
         "preferencia que se establece a continuación: el accionista que pretenda enajenar sus acciones deberá "
         "ofrecerlas primero a los demás accionistas, por conducto del representante legal, quienes dispondrán de "
         "quince (15) días hábiles para manifestar su intención de adquirirlas, a prorrata de su participación. "
         "Vencido este término sin que se ejerza el derecho, el accionista podrá enajenarlas libremente a terceros."),
        ("CAPÍTULO IV — ÓRGANOS DE LA SOCIEDAD", None),
        ("Órganos.", "La sociedad tendrá los siguientes órganos: (i) la asamblea general de accionistas y (ii) el "
         "representante legal. La sociedad no tendrá junta directiva, y las funciones propias de ese órgano serán "
         "ejercidas por el representante legal, conforme al artículo 25 de la Ley 1258 de 2008."),
        ("Asamblea general de accionistas.", "La asamblea general de accionistas está integrada por los accionistas "
         "inscritos en el libro de registro de accionistas. Se reunirá ordinariamente una vez al año, dentro de los "
         "tres (3) primeros meses siguientes al cierre del ejercicio, y extraordinariamente cuando sea convocada por el "
         "representante legal o por accionistas que representen por lo menos el veinte por ciento (20%) de las "
         "acciones suscritas."),
        ("Convocatoria.", "La convocatoria se hará por el representante legal mediante comunicación escrita dirigida a "
         "cada accionista a la dirección de correo electrónico registrada, con una antelación mínima de cinco (5) días "
         "hábiles. La asamblea podrá reunirse sin previa citación y en cualquier lugar cuando estuviere representada la "
         "totalidad de las acciones suscritas."),
        ("Quórum y mayorías.", "La asamblea deliberará con uno o varios accionistas que representen cuando menos la "
         "mitad más una de las acciones suscritas. Las decisiones se adoptarán con el voto favorable de un número "
         "singular o plural de accionistas que represente cuando menos la mitad más una de las acciones presentes, "
         "salvo que la ley o estos estatutos exijan una mayoría superior."),
        ("Reuniones no presenciales.", "Podrán realizarse reuniones no presenciales y adoptarse decisiones por escrito, "
         "en los términos de los artículos 19, 20 y 21 de la Ley 222 de 1995 y del artículo 19 de la Ley 1258 de 2008."),
        ("Representación legal.", "La representación legal de la sociedad estará a cargo de una persona natural, "
         "accionista o no, designada por la asamblea general de accionistas para un término indefinido"
         + (", quien tendrá un suplente que lo reemplazará en sus faltas absolutas, temporales o accidentales."
            if d.get("suplente") else ".")),
        ("Facultades del representante legal.", "El representante legal tendrá a su cargo la administración y gestión "
         "de los negocios sociales, con facultades para ejecutar todos los actos y celebrar todos los contratos "
         "comprendidos dentro del objeto social o que se relacionen directamente con su existencia y funcionamiento, "
         "sin límite de cuantía. Requerirá autorización previa de la asamblea general de accionistas para enajenar o "
         "gravar bienes inmuebles de la sociedad y para constituir garantías reales sobre los mismos."),
        ("CAPÍTULO V — ESTADOS FINANCIEROS Y UTILIDADES", None),
        ("Ejercicio social.", "El ejercicio social será anual y se cerrará el treinta y uno (31) de diciembre de cada año. "
         "A esa fecha se prepararán y difundirán los estados financieros de propósito general, de conformidad con las "
         "normas de contabilidad y de información financiera aceptadas en Colombia."),
        ("Reserva legal.", "La sociedad constituirá una reserva legal equivalente al cincuenta por ciento (50%) del "
         "capital suscrito, formada con el diez por ciento (10%) de las utilidades líquidas de cada ejercicio, en los "
         "términos del artículo 85 de la Ley 1258 de 2008 en concordancia con las normas del Código de Comercio."),
        ("Distribución de utilidades.", "Las utilidades se distribuirán con base en los estados financieros aprobados "
         "por la asamblea general de accionistas, en proporción a las acciones suscritas de cada accionista, previa "
         "apropiación de la reserva legal y de las demás reservas que decida constituir la asamblea."),
        ("Revisor fiscal.", "La sociedad no está obligada a tener revisor fiscal. En el evento en que llegue a superar los "
         "topes previstos en el parágrafo 2º del artículo 13 de la Ley 43 de 1990 o en las normas que lo sustituyan, la "
         "asamblea general de accionistas procederá a designarlo."),
        ("CAPÍTULO VI — DISOLUCIÓN Y LIQUIDACIÓN", None),
        ("Causales de disolución.", "La sociedad se disolverá por las causales previstas en el artículo 34 de la Ley 1258 "
         "de 2008 y por las demás establecidas en la ley. Podrá evitarse la disolución mediante la adopción de las "
         "medidas a que hubiere lugar, dentro de los seis (6) meses siguientes a la fecha en que la asamblea reconozca "
         "el acaecimiento de la causal."),
        ("Liquidación.", "Disuelta la sociedad, se procederá a su liquidación conforme al procedimiento señalado para la "
         "liquidación de las sociedades de responsabilidad limitada. Actuará como liquidador el representante legal, "
         "salvo que la asamblea general de accionistas designe a persona distinta."),
        ("CAPÍTULO VII — DISPOSICIONES FINALES", None),
        ("Resolución de conflictos.", "Las diferencias que ocurran entre los accionistas, o entre estos y la sociedad o "
         "sus administradores, con ocasión del contrato social, serán sometidas a conciliación ante un centro de "
         "conciliación legalmente autorizado del domicilio social. De no lograrse acuerdo, las partes acudirán a la "
         "justicia ordinaria o a la Superintendencia de Sociedades, según corresponda."),
        ("Remisión normativa.", "En lo no previsto en los presentes estatutos, la sociedad se regirá por la Ley 1258 de "
         "2008, por las normas que la modifiquen o complementen y, en su defecto, por las disposiciones del Código de "
         "Comercio aplicables a la sociedad anónima."),
    ]
    n = 0
    for tit, txt in art:
        if txt is None:
            s.append(Paragraph(tit, E["cap"]))
        else:
            n += 1
            s.append(Paragraph(f"<b>ARTÍCULO {n}. {tit}</b> {txt}", E["p"]))

    # disposiciones transitorias
    s.append(Paragraph("DISPOSICIONES TRANSITORIAS", E["cap"]))
    s.append(Paragraph(f"<b>PRIMERA. Suscripción y pago del capital.</b> El capital suscrito y pagado de "
                       f"{_pesos_letras(capital)}, representado en {en_letras(n_acc)} ({_num(n_acc)}) acciones ordinarias "
                       f"de valor nominal de {_pesos_letras(vn)} cada una, queda íntegramente suscrito y pagado en "
                       "dinero efectivo en este acto, así:", E["p"]))
    filas = [["ACCIONISTA", "ACCIONES", "VALOR"]]
    for a in acc:
        k = int(a["acciones"])
        filas.append([Paragraph(f"{a['nombre'].upper()} · C.C. {a['cedula']}", E["p"]),
                      _num(k), _pesos(k * vn)])
    filas.append(["TOTAL", _num(n_acc), _pesos(capital)])
    t = Table(filas, colWidths=[9.5 * cm, 3 * cm, 4 * cm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, (0.6, 0.6, 0.6)),
                           ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    s += [t, Spacer(1, 8)]

    def quien(x):
        if isinstance(x, int):
            return acc[x]
        return x

    rl = quien(d["representante"])
    sup = quien(d["suplente"]) if d.get("suplente") not in (None, "") else None
    nombra = "La accionista única designa" if uno else "Los accionistas designan"
    txt = (f"{nombra} como <b>REPRESENTANTE LEGAL</b> de la sociedad a {rl['nombre'].upper()}, identificado(a) con "
           f"cédula de ciudadanía número {rl['cedula']}" + (f" expedida en {rl['expedida']}" if rl.get("expedida") else ""))
    if sup:
        txt += (f", y como <b>SUPLENTE</b> a {sup['nombre'].upper()}, identificado(a) con cédula de ciudadanía número "
                f"{sup['cedula']}" + (f" expedida en {sup['expedida']}" if sup.get("expedida") else ""))
    txt += (", quienes" if sup else ", quien") + " por documento separado manifiesta" + ("n" if sup else "") + \
        " su aceptación al cargo."
    s.append(Paragraph("<b>SEGUNDA. Nombramientos.</b> " + txt, E["p"]))
    s.append(Paragraph(f"<b>TERCERA. Notificaciones.</b> La sociedad recibirá notificaciones en la dirección "
                       f"{d['direccion']}, del municipio de {lugar}, en el correo electrónico {d['correo']}"
                       + (f" y en el teléfono {d['telefono']}" if d.get("telefono") else "") + ".", E["p"]))
    s.append(Paragraph("<b>CUARTA. Facultad de trámite.</b> Se faculta al representante legal para realizar todos los "
                       "trámites necesarios ante la Cámara de Comercio, la Dirección de Impuestos y Aduanas Nacionales y "
                       "demás entidades, tendientes a obtener la matrícula mercantil, el registro único tributario y el "
                       "registro de los libros de comercio de la sociedad.", E["p"]))
    cierre = ("En constancia de lo anterior, " + ("la compareciente suscribe" if uno else "los comparecientes suscriben")
              + f" el presente documento privado de constitución en el municipio de {lugar}, {_fecha_letras(f)}, "
              "en dos (2) ejemplares del mismo tenor.")
    s.append(Paragraph(cierre, E["p"]))
    for a in acc:
        s.append(KeepTogether([Spacer(1, 34), Paragraph("_________________________________", E["c"]),
                               Paragraph(f"<b>{a['nombre'].upper()}</b>", E["c"]),
                               Paragraph(f"C.C. {a['cedula']}" + (f" de {a['expedida']}" if a.get("expedida") else ""),
                                         E["c"])]))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=2.5 * cm, rightMargin=2.5 * cm,
                            topMargin=2.2 * cm, bottomMargin=2.2 * cm,
                            title=f"Constitución {razon}", author="Tributando.co")
    pie = _marca if borrador else (lambda c, dc: None)
    doc.build(s, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()
