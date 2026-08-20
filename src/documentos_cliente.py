"""Documentos que el CLIENTE debe entregarle al CONTADOR, según su exógena.

A diferencia del checklist genérico (src/documentos.py), aquí la lista se arma
partida por partida: si la exógena trae rendimientos de BANCOLOMBIA, se pide el
certificado tributario DE BANCOLOMBIA — solo lo que ese cliente necesita.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

AZUL = colors.HexColor("#1e2432")
ORO = colors.HexColor("#b8862f")
GRIS = colors.HexColor("#5a6b7f")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _titulo(nombre: str) -> str:
    """Nombre del tercero presentable (Title Case, sin colas jurídicas ruidosas)."""
    n = (nombre or "").strip()
    if not n:
        return "la entidad"
    # nombres jurídicos kilométricos: cortar en la cola ("pudiendo utilizar…")
    n = re.split(r"(?i)\s+pudiendo\b|,", n)[0].strip()
    if len(n) > 48:
        n = n[:48].rsplit(" ", 1)[0] + "…"
    return " ".join(w if w.isupper() and len(w) <= 4 else w.capitalize()
                    for w in n.title().split())


# (patrón sobre el detalle normalizado, categoría, plantilla del documento)
# {ent} = nombre del informante. El orden importa: gana la primera que calce.
_REGLAS = [
    (r"salario|prestaciones sociales|cesantias e intereses|rentas de trabajo|emolument",
     "Ingresos laborales",
     "Certificado de ingresos y retenciones (Formulario 220) de {ent}"),
    (r"pagos? por pension|mesada", "Ingresos por pensión",
     "Certificado de ingresos y retenciones de la pensión — {ent}"),
    (r"honorarios|comisiones|servicios|arrendamient", "Otros ingresos",
     "Certificado de retención en la fuente (o facturas) de {ent}"),
    (r"dividendos|participacion", "Dividendos",
     "Certificado de dividendos y participaciones de {ent}"),
    (r"cdt", "Inversiones",
     "Certificado del CDT en {ent} (saldo a 31 dic y rendimientos)"),
    (r"rendimientos|intereses pagados", "Rendimientos financieros",
     "Certificado tributario de {ent} (rendimientos y retenciones)"),
    (r"saldo cuentas bancarias|movimientos en cuentas", "Cuentas bancarias",
     "Certificado tributario de {ent} con el saldo a 31 de diciembre"),
    (r"avaluo catastral", "Inmuebles",
     "Impuesto predial (o escritura) del inmueble en {ent}"),
    (r"fondo de cesantia|cesantias abonadas|cesantias consignadas", "Cesantías",
     "Certificado del fondo de cesantías {ent}"),
    (r"pension voluntaria|aportes? voluntarios|ahorro voluntario|\bafc\b", "Ahorro voluntario",
     "Certificado del fondo voluntario / cuenta AFC en {ent} (aportes, retiros y saldo)"),
    (r"medicina prepagada|plan complementario|poliza de salud", "Salud",
     "Certificado anual de pagos de medicina prepagada — {ent}"),
    (r"interes.*(vivienda|hipotec|leasing)|credito.*vivienda", "Crédito de vivienda",
     "Certificado de intereses del crédito de vivienda / leasing — {ent}"),
    (r"deuda a cargo|saldo.*deuda|pasivo", "Deudas",
     "Certificado del saldo de la deuda a 31 de diciembre — {ent}"),
    (r"tarjeta (de )?credito|tarjeta.*debito", "Consumos (informativo)",
     "Extractos de la tarjeta en {ent} (solo si va a soportar gastos o el patrimonio)"),
    (r"enajenacion|venta de|escritura|notaria", "Ventas de bienes",
     "Escritura o contrato de la venta reportada por {ent}"),
    (r"retencion practicada|retencion prácticada", "Retenciones",
     "Certificado de retenciones practicadas por {ent}"),
]

_BASICOS = [
    "Usuario y clave del portal DIAN (o disposición para crear la firma electrónica).",
]


def documentos_de(exogena) -> List[dict]:
    """[{categoria, documento}] deducidos de las partidas — sin duplicados."""
    docs, vistos = [], set()
    declaro_antes = False
    for p in exogena.partidas:
        det = _norm(p.detalle)
        if "patrimonio bruto declarado en el ano anterior" in det and (p.valor or 0) > 0:
            declaro_antes = True
        # filas propias o de la DIAN no piden documento a un tercero
        inf = _norm(p.informante_nombre)
        if not inf or "direccion de impuestos" in inf or \
                p.informante_nit == exogena.identificacion:
            continue
        if "a favor contribuyente" in det or "a favor del contribuyente" in det:
            continue                     # activo informativo, no es un documento
        for patron, cat, plantilla in _REGLAS:
            if re.search(patron, det):
                doc = plantilla.format(ent=_titulo(p.informante_nombre))
                clave = (cat, p.informante_nit or inf,
                         plantilla.split("{")[0])
                if clave not in vistos:
                    vistos.add(clave)
                    docs.append({"categoria": cat, "documento": doc,
                                 "entidad": _titulo(p.informante_nombre)})
                break
    # Un banco entrega UN solo certificado tributario que ya trae saldos,
    # rendimientos, retención en la fuente y deudas: se consolida por entidad.
    _FIN = {"Cuentas bancarias", "Rendimientos financieros", "Deudas",
            "Inversiones", "Consumos (informativo)"}
    financieras, resto = {}, []
    for d in docs:
        if d["categoria"] in _FIN:
            financieras.setdefault(d["entidad"], set()).add(d["categoria"])
        else:
            resto.append(d)
    ya_cubiertas = {d["entidad"] for d in resto
                    if d["categoria"] in ("Ahorro voluntario", "Cesantías")}
    for ent in financieras:
        if ent in ya_cubiertas:
            continue                     # su certificado de fondo ya lo trae todo
        resto.append({"categoria": "Bancos y entidades financieras", "entidad": ent,
                      "documento": f"Certificado tributario de {ent} — incluye saldos a "
                                   "31 de diciembre, rendimientos, retención en la fuente "
                                   "y saldos de deudas"})
    docs = resto

    # El empleador que expide el F220 ya cubre sus cesantías/aportes/deudas.
    empleadores = {d["documento"].rsplit(" de ", 1)[-1] for d in docs
                   if d["categoria"] == "Ingresos laborales"}
    docs = [d for d in docs if d["categoria"] == "Ingresos laborales"
            or not any(e and e in d["documento"] for e in empleadores)]

    # Retenciones sueltas: si esa entidad ya entrega otro certificado, sobra.
    entidades_con_doc = {d["documento"].split(" de ")[-1] for d in docs
                         if d["categoria"] != "Retenciones"}
    docs = [d for d in docs if d["categoria"] != "Retenciones"
            or not any(e in d["documento"] for e in entidades_con_doc)]

    basicos = list(_BASICOS)
    out = [{"categoria": "Básicos", "documento": b} for b in basicos]
    out += sorted(({"categoria": d["categoria"], "documento": d["documento"]}
                   for d in docs), key=lambda d: d["categoria"])
    return out


def texto_whatsapp(exogena, contador: str = "") -> str:
    """Mensaje listo para que el contador se lo mande al cliente."""
    nombre = (exogena.nombre or "").split()[-1].title() if exogena.nombre else ""
    lineas = [f"Hola{' ' + nombre if nombre else ''} 👋 Para preparar tu declaración "
              f"de renta necesito que me hagas llegar estos documentos:", ""]
    cat_actual = None
    for d in documentos_de(exogena):
        if d["categoria"] != cat_actual:
            cat_actual = d["categoria"]
            lineas.append(f"*{cat_actual}*")
        lineas.append(f"☐ {d['documento']}")
    lineas += ["", "Con eso dejamos tu declaración lista a tiempo. ¡Gracias!"]
    if contador:
        lineas.append(f"— {contador}")
    return "\n".join(lineas)


def generar_pdf(ruta: Path, exogena, contador: str = "",
                logo_bytes: Optional[bytes] = None) -> None:
    """PDF con la lista personalizada, con la marca del contador si la tiene."""
    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    est = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=est["Title"], textColor=AZUL,
                        fontSize=17, spaceAfter=2, alignment=0)
    sub = ParagraphStyle("sub", parent=est["Normal"], textColor=GRIS, fontSize=9.5)
    hcat = ParagraphStyle("hcat", parent=est["Heading3"], textColor=ORO,
                          fontSize=11.5, spaceBefore=10, spaceAfter=2)
    item = ParagraphStyle("item", parent=est["Normal"], fontSize=10,
                          leftIndent=14, spaceAfter=3)

    hoy = date.today().strftime("%d/%m/%Y")
    quien = f" — {contador}" if contador else ""
    flujo = [
        Paragraph("Documentos para su declaración de renta", h1),
        Paragraph(f"{_titulo(exogena.nombre)} · C.C./NIT {exogena.identificacion} · "
                  f"año gravable {exogena.anio or ''} · generado el {hoy}{quien}", sub),
        Spacer(1, 4), HRFlowable(width="100%", color=ORO, thickness=1.4), Spacer(1, 6),
        Paragraph("Esta lista se armó con la información que terceros reportaron a la "
                  "DIAN sobre usted: solo se pide lo que su caso necesita.", sub),
    ]
    cat_actual = None
    for d in documentos_de(exogena):
        if d["categoria"] != cat_actual:
            cat_actual = d["categoria"]
            flujo.append(Paragraph(cat_actual, hcat))
        flujo.append(Paragraph(f"☐&nbsp;&nbsp;{d['documento']}", item))
    flujo += [Spacer(1, 10), HRFlowable(width="100%", color=colors.HexColor("#e6e9ef")),
              Paragraph("Generado con tributando.co", ParagraphStyle(
                  "pie", parent=sub, fontSize=8, alignment=2))]
    doc.build(flujo)
