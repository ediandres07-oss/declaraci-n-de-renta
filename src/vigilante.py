"""Vigilante DIAN: detecta en la exógena de una persona lo que parece NO ser suyo.

Reglas deterministas (sin IA, sin costo por consulta) sacadas de casos reales:
- Un tercero reportó $210 M de «pagos por servicios» sin retención y con un «ingreso
  laboral promedio» de $105 M/mes, incoherente con lo anual → reporte falso.
- El mismo proveedor reportó los honorarios y además los documentos soporte que
  suman exactamente lo mismo → doble reporte.
- Consignaciones muy por encima de los ingresos reales → riesgo de fiscalización.
- Inmuebles y vehículos a nombre de la persona que no son suyos (sucesiones,
  sociedades conyugales liquidadas) → hay que confirmarlos.

`analizar(resultado)` recibe el ResultadoExogena de `exogena_parser` y devuelve
un dict con el resumen y la lista de alertas (nivel alta/media/revisar).
"""
from __future__ import annotations

import re
from collections import defaultdict

UMBRAL_SIN_RETENCION = 10_000_000     # ingreso de un tercero sin retención
UMBRAL_BIEN = 5_000_000               # bienes que vale la pena confirmar

_RX_INGRESO_SERVICIO = re.compile(
    r"pagos?\s+por\s+(servicios|honorarios|comisiones|arrendamientos)|"
    r"compras?\s+de\s+activos|otros\s+pagos", re.I)
_RX_LABORAL = re.compile(r"salarios|prestaciones|cesant|vacaciones|pensi", re.I)
_RX_RETENCION = re.compile(r"retenci", re.I)
_RX_PROMEDIO = re.compile(r"ingreso\s+laboral\s+promedio", re.I)
_RX_BIEN = re.compile(r"aval[uú]o|notar|adquisici[oó]n\s+de\s+bienes|veh[ií]culo", re.I)
_RX_NO_DUP = re.compile(r"rendimiento|intereses|consumos|movimientos|saldo", re.I)


def _pesos(v: float) -> str:
    return "$" + f"{v:,.0f}".replace(",", ".")


def analizar(res) -> dict:
    partidas = list(res.partidas or [])
    topes = res.topes_dian or {}
    alertas: list[dict] = []

    por_inf = defaultdict(list)
    for p in partidas:
        por_inf[p.informante_nit].append(p)
    con_retencion = {nit for nit, ps in por_inf.items()
                     if any(_RX_RETENCION.search(p.detalle or "") and (p.valor or 0) > 0 for p in ps)}

    sospechosos_valor = 0.0

    # A · ingreso alto sin retención (servicios, honorarios, comisiones…)
    #     Alta si lo reporta una PERSONA NATURAL (el caso típico del reporte falso);
    #     media si es una empresa o entidad. No aplica a comisiones del empleador.
    empleadores = {nit for nit, ps in por_inf.items()
                   if any(re.search(r"salarios", p.detalle or "", re.I) for p in ps)}
    for p in partidas:
        d = p.detalle or ""
        if (1 in (p.topes or []) and _RX_INGRESO_SERVICIO.search(d)
                and not _RX_LABORAL.search(d) and (p.valor or 0) >= UMBRAL_SIN_RETENCION
                and p.informante_nit not in con_retencion
                and p.informante_nit not in empleadores):
            persona = not str(p.informante_nit or "").startswith(("8", "9"))
            prom = sum(q.valor or 0 for q in por_inf[p.informante_nit]
                       if _RX_PROMEDIO.search(q.detalle or ""))
            extra = (f" Además dice que tu ingreso promedio de los últimos seis meses es "
                     f"{_pesos(prom)} al mes." if prom else "")
            if persona:
                sospechosos_valor += p.valor
                alertas.append({
                    "nivel": "alta",
                    "titulo": f"{p.informante_nombre} te reporta {_pesos(p.valor)} y no te retuvo nada",
                    "detalle": (f"Concepto: {d}. Es una persona natural, y un pago así casi "
                                "siempre lleva retención en la fuente: que no haya ninguna es la "
                                "huella típica de un reporte falso o de un error." + extra),
                    "que_hacer": ("Si no le prestaste ese servicio, pídele por escrito que corrija "
                                  "su exógena (Formato 1001) y guarda la solicitud: es tu soporte "
                                  "ante la DIAN. No lo declares como ingreso."),
                    "informante": f"{p.informante_nombre} · NIT {p.informante_nit}",
                    "valor": p.valor})
            else:
                alertas.append({
                    "nivel": "media",
                    "titulo": f"{p.informante_nombre} te reporta {_pesos(p.valor)} sin retención",
                    "detalle": f"Concepto: {d}." + extra,
                    "que_hacer": ("Confirma que sí le prestaste ese servicio y que el valor es el "
                                  "que te pagaron. Si te retuvieron, pide el certificado: esa "
                                  "retención te la descuentas en la renta."),
                    "informante": f"{p.informante_nombre} · NIT {p.informante_nit}",
                    "valor": p.valor})

    # C · mismo tercero, un valor igual a la suma de otros → doble reporte
    for nit, ps in por_inf.items():
        vals = [p for p in ps if (p.valor or 0) >= 1_000_000 and not _RX_NO_DUP.search(p.detalle or "")
                and (1 in (p.topes or []) or set(p.renglones or []) & {74, 77})]
        for p in vals:
            otros = [q for q in ps if q is not p and (q.detalle or "") != (p.detalle or "")
                     and 0 < (q.valor or 0) < p.valor and not _RX_NO_DUP.search(q.detalle or "")]
            por_det = defaultdict(float)
            for q in otros:
                por_det[q.detalle] += q.valor
            for det, suma in por_det.items():
                if abs(suma - p.valor) <= 1 and sum(1 for q in otros if q.detalle == det) >= 2:
                    alertas.append({
                        "nivel": "media",
                        "titulo": f"{p.informante_nombre} parece reportarte lo mismo dos veces",
                        "detalle": (f"«{p.detalle}» por {_pesos(p.valor)} y, aparte, varias filas de "
                                    f"«{det}» que suman exactamente {_pesos(suma)}."),
                        "que_hacer": "En tu declaración cuéntalo una sola vez y conserva los soportes del pago real.",
                        "informante": f"{p.informante_nombre} · NIT {nit}", "valor": p.valor})

    # D · consignaciones muy por encima de los ingresos reales
    ingresos = float(topes.get("ingresos") or 0) - sospechosos_valor
    consig = float(topes.get("consignaciones") or 0)
    if consig and consig > 4 * max(ingresos, 1):
        alertas.append({
            "nivel": "media",
            "titulo": "Tus consignaciones son muy altas frente a tus ingresos",
            "detalle": (f"Los bancos reportan {_pesos(consig)} en movimientos y tus ingresos "
                        f"reportados (sin lo sospechoso) son {_pesos(ingresos)}: "
                        f"{consig / max(ingresos, 1):.1f} veces. Es el disparador más común de un "
                        "requerimiento de la DIAN."),
            "que_hacer": ("Ten a mano la explicación: plata de terceros que pasó por tu cuenta, "
                          "préstamos, traslados entre tus propias cuentas o ventas de bienes."),
            "informante": "Bancos", "valor": consig})

    # E · bienes a tu nombre (y avalúos repetidos)
    vistos = defaultdict(int)
    for p in partidas:
        if _RX_BIEN.search(p.detalle or "") and (p.valor or 0) >= UMBRAL_BIEN:
            vistos[(p.informante_nit, round(p.valor))] += 1
    for p in partidas:
        clave = (p.informante_nit, round(p.valor or 0))
        if clave in vistos and vistos[clave] > 0:
            rep = vistos.pop(clave)
            alertas.append({
                "nivel": "revisar",
                "titulo": (f"{p.informante_nombre}: {_pesos(p.valor)}"
                           + (f" (aparece {rep} veces)" if rep > 1 else "")),
                "detalle": f"{p.detalle}. Queda en tu patrimonio a los ojos de la DIAN.",
                "que_hacer": ("Confirma que ese bien es tuyo y sigue a tu nombre. Si lo vendiste, "
                              "si es de una sucesión o de una sociedad conyugal ya liquidada, o si "
                              "aparece repetido, hay que corregirlo con quien lo reporta."),
                "informante": f"{p.informante_nombre} · NIT {p.informante_nit}",
                "valor": p.valor})

    orden = {"alta": 0, "media": 1, "revisar": 2}
    alertas.sort(key=lambda a: (orden[a["nivel"]], -(a["valor"] or 0)))
    cuenta = {k: sum(1 for a in alertas if a["nivel"] == k) for k in orden}
    return {"nombre": res.nombre, "identificacion": res.identificacion, "anio": res.anio,
            "alertas": alertas, "cuenta": cuenta,
            "valor_sospechoso": sospechosos_valor}
