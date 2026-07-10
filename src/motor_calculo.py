"""Motor de cálculo del Formulario 210 (personas naturales residentes).

Reproduce, renglón por renglón, la liquidación privada:
  cédula general (trabajo, honorarios, capital, no laborales) con el límite
  del 40% / 1.340 UVT, cédula de pensiones, cédula de dividendos, ganancias
  ocasionales, impuesto según la tabla del Art. 241 E.T., descuentos,
  retenciones, anticipos y saldo a pagar / a favor.

La numeración de renglones sigue el Formulario 210 oficial (29–141), igual
que la hoja 'FORMULARIO 210' de la plantilla ITGS usada como destino.

Todo parámetro normativo (UVT, tarifas, topes) proviene de Parametros; el
motor no conoce cifras del año.
"""
from typing import Dict, Tuple

from .modelos import DatosDeclaracion, Liquidacion, SubcedulaGeneral
from .parametros import Parametros


def _round_mil(v: float) -> float:
    """La plantilla redondea varios renglones al múltiplo de mil (ROUND(x,-3))."""
    return round(v / 1000.0) * 1000.0


def _liquidar_subcedula(s: SubcedulaGeneral, con_devoluciones: bool = False) -> Dict[str, float]:
    """Renta líquida de una subcédula antes del límite de exenciones."""
    base = s.ingresos_brutos - s.incrngo - s.costos_deducciones
    if con_devoluciones:
        base -= s.devoluciones
    return {
        "renta_liquida": max(0.0, base + s.rentas_pasivas_ece),
        "perdida": max(0.0, -(base + s.rentas_pasivas_ece)),
    }


def calcular_renta_exenta_25(datos: DatosDeclaracion, p: Parametros) -> float:
    """Renta exenta laboral del 25% (Art. 206 num. 10 E.T.).

    Se calcula sobre el ingreso laboral depurado (ingresos - INCRNGO - demás
    rentas exentas - deducciones imputables), con tope anual en UVT.
    """
    t = datos.trabajo
    base = (t.ingresos_brutos - t.incrngo - t.total_rentas_exentas - t.total_deducciones)
    if base <= 0:
        return 0.0
    exenta = base * p.exenta_25_porcentaje
    return min(exenta, p.a_pesos(p.exenta_25_tope_uvt))


def calcular(datos: DatosDeclaracion, p: Parametros) -> Liquidacion:
    liq = Liquidacion()
    d = datos

    # =============================== Patrimonio ==========================
    liq.set(29, d.patrimonio_bruto, "patrimonio bruto")
    liq.set(30, d.deudas, "deudas")
    liq.set(31, max(0.0, d.patrimonio_bruto - d.deudas), "patrimonio líquido")

    # ===================== Renglón 28: 1% factura electrónica ============
    r28 = min(d.compras_factura_electronica * p.factura_electronica_pct,
              p.a_pesos(p.factura_electronica_tope_uvt))
    r28 = _round_mil(r28)
    liq.set(28, r28, "1% compras con factura electrónica (tope "
            f"{p.factura_electronica_tope_uvt:,.0f} UVT)")

    # ======================= Cédula general: subcédulas ==================
    # Renta exenta 25% laboral automática (se agrega a 'otras rentas exentas')
    exenta_25 = 0.0
    if d.aplicar_renta_exenta_25:
        exenta_25 = calcular_renta_exenta_25(d, p)
        if exenta_25 > 0:
            liq.detalle.append(f"Renta exenta 25% laboral (Art. 206-10): {exenta_25:,.0f}")

    t, h, c, nl = d.trabajo, d.honorarios, d.capital, d.no_laboral

    # --- rentas de trabajo (R32-R42) ---
    liq.set(32, t.ingresos_brutos, "ingresos brutos rentas de trabajo")
    liq.set(33, t.incrngo, "INCRNGO rentas de trabajo")
    r34 = max(0.0, t.ingresos_brutos - t.incrngo - t.costos_deducciones)
    liq.set(34, r34, "renta líquida rentas de trabajo")
    liq.set(35, t.rentas_exentas_afc_fvp, "aportes AFC/FVP/AVC")
    r36 = t.otras_rentas_exentas + exenta_25
    liq.set(36, r36, "otras rentas exentas (incluye 25% si aplica)")
    r37 = t.rentas_exentas_afc_fvp + r36
    liq.set(37, r37, "total rentas exentas trabajo")
    liq.set(38, t.intereses_vivienda, "intereses de vivienda")
    liq.set(39, t.otras_deducciones, "otras deducciones imputables")
    r40 = t.intereses_vivienda + t.otras_deducciones
    liq.set(40, r40, "total deducciones imputables trabajo")

    # --- honorarios (R43-R57) ---
    liq.set(43, h.ingresos_brutos, "ingresos brutos honorarios")
    liq.set(44, h.incrngo, "INCRNGO honorarios")
    liq.set(45, h.costos_deducciones, "costos y deducciones honorarios")
    r46 = max(0.0, h.ingresos_brutos - h.incrngo - h.costos_deducciones)
    liq.set(46, r46, "renta líquida honorarios")
    liq.set(47, h.rentas_exentas_afc_fvp, "aportes AFC/FVP honorarios")
    liq.set(48, h.otras_rentas_exentas, "otras rentas exentas honorarios")
    r49 = h.total_rentas_exentas
    liq.set(49, r49, "total rentas exentas honorarios")
    liq.set(50, h.intereses_vivienda, "intereses vivienda honorarios")
    liq.set(51, h.otras_deducciones, "otras deducciones honorarios")
    r52 = h.total_deducciones
    liq.set(52, r52, "total deducciones honorarios")

    # --- rentas de capital (R58-R73) ---
    liq.set(58, c.ingresos_brutos, "ingresos brutos rentas de capital")
    liq.set(59, c.incrngo, "INCRNGO rentas de capital")
    liq.set(60, c.costos_deducciones, "costos y deducciones capital")
    r61 = max(0.0, c.ingresos_brutos - c.incrngo - c.costos_deducciones)
    liq.set(61, r61, "renta líquida capital")
    liq.set(62, c.rentas_pasivas_ece, "rentas pasivas ECE capital")
    liq.set(63, c.rentas_exentas_afc_fvp, "aportes AFC/FVP capital")
    liq.set(64, c.otras_rentas_exentas, "otras rentas exentas capital")
    r65 = c.total_rentas_exentas
    liq.set(65, r65, "total rentas exentas capital")
    liq.set(66, c.intereses_vivienda, "intereses vivienda capital")
    liq.set(67, c.otras_deducciones, "otras deducciones capital")
    r68 = c.total_deducciones
    liq.set(68, r68, "total deducciones capital")

    # --- rentas no laborales (R74-R90) ---
    liq.set(74, nl.ingresos_brutos, "ingresos brutos no laborales")
    liq.set(75, nl.devoluciones, "devoluciones, rebajas y descuentos")
    liq.set(76, nl.incrngo, "INCRNGO no laborales")
    liq.set(77, nl.costos_deducciones, "costos y deducciones no laborales")
    r78 = max(0.0, nl.ingresos_brutos - nl.devoluciones - nl.incrngo - nl.costos_deducciones)
    liq.set(78, r78, "renta líquida no laborales")
    liq.set(79, nl.rentas_pasivas_ece, "rentas pasivas ECE no laborales")
    liq.set(80, nl.rentas_exentas_afc_fvp, "aportes AFC/FVP no laborales")
    liq.set(81, nl.otras_rentas_exentas, "otras rentas exentas no laborales")
    r82 = nl.total_rentas_exentas
    liq.set(82, r82, "total rentas exentas no laborales")
    liq.set(83, nl.intereses_vivienda, "intereses vivienda no laborales")
    liq.set(84, nl.otras_deducciones, "otras deducciones no laborales")
    r85 = nl.total_deducciones
    liq.set(85, r85, "total deducciones no laborales")

    # ================= Límite 40% / 1.340 UVT (Art. 336 E.T.) ============
    ingresos_cg = (t.ingresos_brutos + h.ingresos_brutos + c.ingresos_brutos
                   + nl.ingresos_brutos)
    incrngo_cg = t.incrngo + h.incrngo + c.incrngo + nl.incrngo
    costos_cg = (t.costos_deducciones + h.costos_deducciones + c.costos_deducciones
                 + nl.costos_deducciones)
    base_limite = max(0.0, ingresos_cg - nl.devoluciones - incrngo_cg - costos_cg)
    limite_40 = base_limite * p.limite_40_porcentaje
    limite_uvt = _round_mil(p.a_pesos(p.limite_40_tope_uvt))
    limite = min(limite_40, limite_uvt)
    liq.detalle.append(
        f"Límite Art. 336: min(40% de {base_limite:,.0f} = {limite_40:,.0f}; "
        f"{p.limite_40_tope_uvt:,.0f} UVT = {limite_uvt:,.0f}) = {limite:,.0f}"
    )

    # Distribución en cascada (como la plantilla): trabajo → honorarios →
    # capital → no laborales. Cada subcédula toma del cupo disponible.
    reclamado = {
        "trabajo": r37 + r40,
        "honorarios": r49 + r52,
        "capital": r65 + r68,
        "no_laboral": r82 + r85,
    }
    disponible = limite
    limitado: Dict[str, float] = {}
    for clave in ("trabajo", "honorarios", "capital", "no_laboral"):
        limitado[clave] = min(reclamado[clave], disponible)
        disponible -= limitado[clave]

    liq.set(41, limitado["trabajo"], "rentas exentas/deducciones limitadas trabajo")
    liq.set(53, limitado["honorarios"], "rentas exentas/deducciones limitadas honorarios")
    liq.set(69, limitado["capital"], "rentas exentas/deducciones limitadas capital")
    liq.set(86, limitado["no_laboral"], "rentas exentas/deducciones limitadas no laborales")

    # --- rentas líquidas ordinarias por subcédula ---
    r42 = max(0.0, r34 - limitado["trabajo"])
    liq.set(42, r42, "renta líquida ordinaria trabajo")

    r54 = max(0.0, r46 - limitado["honorarios"])
    liq.set(54, r54, "renta líquida ordinaria del ejercicio honorarios")
    liq.set(55, max(0.0, -(h.ingresos_brutos - h.incrngo - h.costos_deducciones)),
            "pérdida líquida honorarios")
    liq.set(56, h.compensaciones, "compensaciones honorarios")
    r57 = max(0.0, r54 - h.compensaciones)
    liq.set(57, r57, "renta líquida ordinaria honorarios")

    r70 = max(0.0, r61 + c.rentas_pasivas_ece - limitado["capital"])
    liq.set(70, r70, "renta líquida ordinaria del ejercicio capital")
    liq.set(71, max(0.0, -(c.ingresos_brutos + c.rentas_pasivas_ece - c.incrngo
                           - c.costos_deducciones)), "pérdida líquida capital")
    liq.set(72, c.compensaciones, "compensaciones capital")
    r73 = max(0.0, r70 - c.compensaciones)
    liq.set(73, r73, "renta líquida ordinaria capital")

    r87 = max(0.0, r78 + nl.rentas_pasivas_ece - limitado["no_laboral"])
    liq.set(87, r87, "renta líquida ordinaria del ejercicio no laborales")
    liq.set(88, max(0.0, -(nl.ingresos_brutos + nl.rentas_pasivas_ece - nl.devoluciones
                           - nl.incrngo - nl.costos_deducciones)), "pérdida no laborales")
    liq.set(89, nl.compensaciones, "compensaciones no laborales")
    r90 = max(0.0, r87 - nl.compensaciones)
    liq.set(90, r90, "renta líquida ordinaria no laborales")

    # ==================== Totales cédula general (R91-R98) ===============
    # R91: renta líquida de la cédula general (la plantilla suma exentas
    # limitadas + renta ordinaria por subcédula = renta antes del límite)
    r91 = _round_mil((limitado["trabajo"] + r42) + (limitado["honorarios"] + r54)
                     + (limitado["capital"] + r70) + (limitado["no_laboral"] + r87))
    liq.set(91, r91, "renta líquida cédula general")

    # R139: adición por dependientes (72 UVT c/u, máx. 4, fuera del límite 40%)
    n_dep = min(d.dependientes, p.dependientes_max)
    r139 = _round_mil(n_dep * p.a_pesos(p.dependientes_uvt))
    liq.set(138, d.dependientes, "número de dependientes")
    liq.set(139, r139, f"deducción adicional por {n_dep} dependiente(s) x "
            f"{p.dependientes_uvt:,.0f} UVT")

    # R92: exentas y deducciones imputables limitadas + R28 + R139
    r92 = (limitado["trabajo"] + limitado["honorarios"] + limitado["capital"]
           + limitado["no_laboral"] + r28 + r139)
    liq.set(92, r92, "rentas exentas y deducciones imputables (incluye R28 y R139)")

    r93 = max(0.0, r91 - r92)
    liq.set(93, r93, "renta líquida ordinaria cédula general")
    liq.set(94, d.compensaciones_perdidas, "compensaciones pérdidas 2018 y anteriores")
    liq.set(95, d.compensacion_exceso_presuntiva, "compensación exceso renta presuntiva")
    liq.set(96, d.rentas_gravables, "rentas gravables (activos omitidos, etc.)")
    r97 = max(0.0, r93 + d.rentas_gravables - d.compensaciones_perdidas
              - d.compensacion_exceso_presuntiva)
    liq.set(97, r97, "renta líquida gravable cédula general")

    r98 = liq.r(31) * p.renta_presuntiva_tasa
    liq.set(98, r98, f"renta presuntiva ({p.renta_presuntiva_tasa:.0%})")

    # ======================= Cédula de pensiones =========================
    liq.set(99, d.pension_ingresos, "ingresos por pensiones")
    liq.set(100, d.pension_incrngo, "INCRNGO pensiones")
    r101 = max(0.0, d.pension_ingresos - d.pension_incrngo)
    liq.set(101, r101, "renta líquida pensiones")
    r102 = min(d.pension_exenta, r101)
    liq.set(102, r102, "rentas exentas pensiones")
    r103 = max(0.0, r101 - r102)
    liq.set(103, r103, "renta líquida gravable pensiones")

    # ================== Cédula de dividendos (R104-R110) =================
    liq.set(104, d.dividendos_2016_anteriores, "dividendos 2016 y anteriores")
    liq.set(105, d.dividendos_2016_incrngo, "INCRNGO dividendos 2016")
    r106 = max(0.0, d.dividendos_2016_anteriores - d.dividendos_2016_incrngo)
    liq.set(106, r106, "renta líquida dividendos 2016 y anteriores")
    liq.set(107, d.dividendos_sub1, "1a subcédula dividendos 2017+")
    liq.set(108, d.dividendos_sub2, "2a subcédula dividendos 2017+")
    liq.set(109, d.dividendos_exterior, "dividendos del exterior")
    r110 = min(d.dividendos_exterior_exentos, d.dividendos_exterior)
    liq.set(110, r110, "rentas exentas dividendos exterior")

    # ================ Impuestos sobre dividendos (R118-R120) =============
    div_cfg = p.dividendos
    # 1a subcédula: tabla 0% hasta 300 UVT, 10% en adelante
    sub1_uvt = p.a_uvt(d.dividendos_sub1)
    tabla1 = div_cfg["subcedula1_tabla"]
    imp_sub1 = 0.0
    for rango in tabla1:
        hasta = rango.get("hasta_uvt")
        if hasta is None or sub1_uvt <= hasta:
            imp_sub1 = max(0.0, (sub1_uvt - rango["desde_uvt"]) * rango["tarifa"] * p.uvt)
            break
    # 2a subcédula: 35% y el neto pasa por la tabla de la 1a subcédula
    tarifa_35 = div_cfg["subcedula2_tarifa_primera_parte"]
    imp_sub2_a = d.dividendos_sub2 * tarifa_35
    neto_sub2_uvt = p.a_uvt(d.dividendos_sub2 - imp_sub2_a)
    imp_sub2_b = 0.0
    for rango in tabla1:
        hasta = rango.get("hasta_uvt")
        if hasta is None or neto_sub2_uvt <= hasta:
            imp_sub2_b = max(0.0, (neto_sub2_uvt - rango["desde_uvt"]) * rango["tarifa"] * p.uvt)
            break
    r118 = _round_mil(imp_sub1 + imp_sub2_a + imp_sub2_b)
    liq.set(118, r118, "impuesto dividendos 2017+ (1a y 2a subcédula)")

    r119 = _round_mil(p.impuesto_tabla(r106, div_cfg["tabla_2016"]))
    liq.set(119, r119, "impuesto dividendos 2016 y anteriores")

    r120 = _round_mil(max(0.0, (d.dividendos_exterior - r110)) * div_cfg["tarifa_exterior"])
    liq.set(120, r120, "impuesto dividendos del exterior")

    # ============ Renta líquida gravable total (R111) y R116/R117 ========
    r111 = r97 + r103 + d.dividendos_sub1 + d.dividendos_sub2
    liq.set(111, r111, "renta líquida gravable (general + pensiones + dividendos 2017+)")

    base_general = r97 + r103
    base_presuntiva = r98 + r103
    imp_general = _round_mil(p.impuesto_tabla(base_general))
    imp_presuntiva = _round_mil(p.impuesto_tabla(base_presuntiva))
    if imp_general >= imp_presuntiva:
        liq.set(116, imp_general, "impuesto sobre rentas líquidas gravables (Art. 241)")
        liq.set(117, 0.0)
    else:
        liq.set(116, 0.0)
        liq.set(117, imp_presuntiva, "impuesto sobre renta presuntiva + pensiones")

    r121 = liq.r(116) + liq.r(117) + r118 + r119 + r120
    liq.set(121, r121, "total impuesto sobre rentas líquidas gravables")

    # ==================== Ganancias ocasionales ==========================
    liq.set(112, d.go_ingresos, "ingresos por ganancias ocasionales")
    liq.set(113, d.go_costos, "costos ganancias ocasionales")
    liq.set(114, d.go_exentas, "ganancias ocasionales exentas/no gravadas")
    r115 = max(0.0, d.go_ingresos - d.go_costos - d.go_exentas)
    liq.set(115, r115, "ganancias ocasionales gravables")

    go_loterias = min(d.go_loterias, r115)
    go_resto = r115 - go_loterias
    r127 = _round_mil(go_resto * p.go_tarifa_general + go_loterias * p.go_tarifa_loterias)
    liq.set(127, r127, f"impuesto GO ({p.go_tarifa_general:.0%} general, "
            f"{p.go_tarifa_loterias:.0%} loterías)")

    # ==================== Descuentos e impuesto a cargo ==================
    liq.set(122, d.descuento_impuestos_exterior, "descuento impuestos pagados en el exterior")
    liq.set(123, d.descuento_donaciones, "descuento donaciones")
    liq.set(124, d.descuento_dividendos_otros, "descuento dividendos y otros")
    r125 = d.descuento_impuestos_exterior + d.descuento_donaciones + d.descuento_dividendos_otros
    if r125 > r121:
        liq.advertencias.append(
            f"Los descuentos tributarios ({r125:,.0f}) superan el impuesto "
            f"({r121:,.0f}); se limitan al impuesto (Art. 259 E.T.)."
        )
        r125 = r121
    liq.set(125, r125, "total descuentos tributarios")

    r126 = max(0.0, r121 - r125)
    liq.set(126, r126, "impuesto neto de renta")
    liq.set(128, min(d.descuento_go_exterior, r127), "descuento GO exterior")
    r129 = max(0.0, r126 + r127 - liq.r(128))
    liq.set(129, r129, "total impuesto a cargo")

    # ========================= Anticipo (Art. 807) =======================
    liq.set(130, d.anticipo_anterior, "anticipo liquidado año anterior")
    liq.set(131, d.saldo_favor_anterior, "saldo a favor año anterior")
    liq.set(132, d.retenciones, "retenciones año gravable")

    pct = p.anticipo_porcentajes[min(max(d.numero_anio_declaracion, 1), 3)]
    if d.numero_anio_declaracion <= 1:
        base_anticipo = r126 * pct
    else:
        promedio = (r126 + d.impuesto_neto_anio_anterior) / 2.0
        # el contribuyente puede optar por el menor de los dos métodos
        base_anticipo = min(promedio, r126) * pct
    r133 = max(0.0, _round_mil(base_anticipo) - _round_mil(d.retenciones))
    liq.set(133, r133, f"anticipo año siguiente ({pct:.0%}, método más favorable)")

    # ===================== Saldo a pagar / a favor =======================
    saldo = r129 + r133 - liq.r(130) - liq.r(131) - liq.r(132)
    liq.set(134, max(0.0, saldo), "saldo a pagar por impuesto")
    liq.set(135, d.sanciones, "sanciones")
    liq.set(136, max(0.0, saldo) + d.sanciones, "total saldo a pagar")
    liq.set(137, max(0.0, -(saldo + d.sanciones)), "total saldo a favor")
    liq.set(140, 0.0)
    liq.set(141, d.aporte_voluntario_141, "aporte voluntario")

    _validar(liq, d)
    return liq


def _validar(liq: Liquidacion, d: DatosDeclaracion) -> None:
    """Validaciones de consistencia previas a la generación del resultado."""
    negativos = [n for n, v in liq.renglones.items() if v < 0]
    if negativos:
        liq.advertencias.append(f"Renglones con valor negativo: {sorted(negativos)}.")
    if d.patrimonio_bruto <= 0:
        liq.advertencias.append("El patrimonio bruto (R29) quedó en cero: verifique activos.")
    if d.retenciones > 0 and liq.r(32) + liq.r(43) + liq.r(58) + liq.r(74) + d.pension_ingresos == 0:
        liq.advertencias.append("Hay retenciones (R132) pero ningún ingreso declarado.")
    if d.deudas > d.patrimonio_bruto:
        liq.advertencias.append("Las deudas (R30) superan el patrimonio bruto (R29).")
    if liq.r(134) > 0 and liq.r(137) > 0:
        liq.advertencias.append("Inconsistencia interna: saldo a pagar y a favor simultáneos.")


def resumen_texto(liq: Liquidacion, p: Parametros) -> str:
    """Resumen legible de la liquidación privada."""
    lineas = [
        "=" * 64,
        f"LIQUIDACIÓN PRIVADA — FORMULARIO 210 — AG {p.anio_gravable}",
        "=" * 64,
        f"{'Patrimonio bruto (29)':45}{liq.r(29):>18,.0f}",
        f"{'Deudas (30)':45}{liq.r(30):>18,.0f}",
        f"{'Patrimonio líquido (31)':45}{liq.r(31):>18,.0f}",
        "-" * 64,
        f"{'Renta líquida cédula general (91)':45}{liq.r(91):>18,.0f}",
        f"{'Rentas exentas y deducciones limitadas (92)':45}{liq.r(92):>18,.0f}",
        f"{'Renta líquida gravable céd. general (97)':45}{liq.r(97):>18,.0f}",
        f"{'Renta gravable pensiones (103)':45}{liq.r(103):>18,.0f}",
        f"{'Renta líquida gravable total (111)':45}{liq.r(111):>18,.0f}",
        "-" * 64,
        f"{'Impuesto rentas líquidas (116)':45}{liq.r(116):>18,.0f}",
        f"{'Impuesto dividendos (118-120)':45}{liq.r(118)+liq.r(119)+liq.r(120):>18,.0f}",
        f"{'Total impuesto (121)':45}{liq.r(121):>18,.0f}",
        f"{'Descuentos tributarios (125)':45}{liq.r(125):>18,.0f}",
        f"{'Impuesto neto de renta (126)':45}{liq.r(126):>18,.0f}",
        f"{'Impuesto ganancias ocasionales (127)':45}{liq.r(127):>18,.0f}",
        f"{'TOTAL IMPUESTO A CARGO (129)':45}{liq.r(129):>18,.0f}",
        "-" * 64,
        f"{'(-) Anticipo año anterior (130)':45}{liq.r(130):>18,.0f}",
        f"{'(-) Saldo a favor año anterior (131)':45}{liq.r(131):>18,.0f}",
        f"{'(-) Retenciones (132)':45}{liq.r(132):>18,.0f}",
        f"{'(+) Anticipo año siguiente (133)':45}{liq.r(133):>18,.0f}",
        f"{'(+) Sanciones (135)':45}{liq.r(135):>18,.0f}",
        "=" * 64,
        f"{'TOTAL SALDO A PAGAR (136)':45}{liq.r(136):>18,.0f}",
        f"{'TOTAL SALDO A FAVOR (137)':45}{liq.r(137):>18,.0f}",
        "=" * 64,
    ]
    if liq.advertencias:
        lineas.append("ADVERTENCIAS:")
        lineas.extend(f"  ⚠ {a}" for a in liq.advertencias)
    lineas.append(
        "Este es un BORRADOR de apoyo. No reemplaza la asesoría de un contador "
        "o abogado tributarista. Verifique la normativa vigente ante la DIAN."
    )
    return "\n".join(lineas)
