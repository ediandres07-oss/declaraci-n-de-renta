"""Pruebas del motor de cálculo. El caso Elizabeth está verificado a mano."""
import pytest

from src.entrevista import mapear_exogena_a_datos
from src.modelos import DatosDeclaracion, GananciaOcasional, SubcedulaGeneral
from src.motor_calculo import calcular, calcular_renta_exenta_25


# ------------------- tabla Art. 241 ----------------------------------------

@pytest.mark.parametrize("base_uvt, impuesto_uvt_esperado", [
    (0, 0), (1090, 0),                      # rango 0%
    (1700, (1700 - 1090) * 0.19),           # borde 19%
    (4100, (4100 - 1700) * 0.28 + 116),     # borde 28%
    (8670, (8670 - 4100) * 0.33 + 788),     # borde 33%
    (18970, (18970 - 8670) * 0.35 + 2296),  # borde 35%
    (31000, (31000 - 18970) * 0.37 + 5901), # borde 37%
    (40000, (40000 - 31000) * 0.39 + 10352) # rango 39%
])
def test_tabla_art_241(parametros, base_uvt, impuesto_uvt_esperado):
    base = parametros.a_pesos(base_uvt)
    assert parametros.impuesto_tabla(base) == pytest.approx(
        impuesto_uvt_esperado * parametros.uvt, rel=1e-9)


# ------------------- renta exenta 25% --------------------------------------

def test_renta_exenta_25_con_tope(parametros):
    d = DatosDeclaracion(trabajo=SubcedulaGeneral(ingresos_brutos=400_000_000))
    exenta = calcular_renta_exenta_25(d, parametros)
    assert exenta == parametros.a_pesos(parametros.exenta_25_tope_uvt)  # topada en 790 UVT


def test_renta_exenta_25_sin_base(parametros):
    d = DatosDeclaracion(trabajo=SubcedulaGeneral(ingresos_brutos=0))
    assert calcular_renta_exenta_25(d, parametros) == 0


# ------------------- límite 40% / 1.340 UVT --------------------------------

def test_limite_40_por_ciento(parametros):
    """Exenciones reclamadas superan el 40% de la base: se limitan."""
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000,
                                 otras_rentas_exentas=80_000_000),
        aplicar_renta_exenta_25=False,
        patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    assert liq.r(41) == pytest.approx(40_000_000)   # 40% de 100M
    assert liq.r(93) == pytest.approx(60_000_000)


def test_limite_1340_uvt(parametros):
    """Con base muy alta manda el tope absoluto de 1.340 UVT."""
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=1_000_000_000,
                                 otras_rentas_exentas=500_000_000),
        aplicar_renta_exenta_25=False,
        patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    tope = round(parametros.a_pesos(1340) / 1000) * 1000
    assert liq.r(41) == pytest.approx(tope)


def test_cascada_limite_entre_subcedulas(parametros):
    """El cupo se agota en orden trabajo → honorarios → capital → no laboral."""
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=50_000_000, otras_rentas_exentas=30_000_000),
        capital=SubcedulaGeneral(ingresos_brutos=50_000_000, otras_rentas_exentas=30_000_000),
        aplicar_renta_exenta_25=False,
        patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    limite = 0.4 * 100_000_000
    assert liq.r(41) == pytest.approx(30_000_000)            # trabajo toma todo lo suyo
    assert liq.r(69) == pytest.approx(limite - 30_000_000)   # capital toma el resto del cupo


# ------------------- deducciones fuera del límite ---------------------------

def test_dependientes_72_uvt_max_4(parametros):
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000),
        aplicar_renta_exenta_25=False, dependientes=6, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    esperado = round(4 * 72 * parametros.uvt / 1000) * 1000
    assert liq.r(139) == esperado


def test_factura_electronica_1pct_tope_240_uvt(parametros):
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000),
        compras_factura_electronica=5_000_000_000,  # 1% = 50M > tope
        aplicar_renta_exenta_25=False, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    tope = round(240 * parametros.uvt / 1000) * 1000
    assert liq.r(28) == tope


# ------------------- pensiones y dividendos --------------------------------

def test_cedula_pensiones(parametros):
    d = DatosDeclaracion(pension_ingresos=100_000_000, pension_exenta=80_000_000,
                         patrimonio_bruto=1)
    liq = calcular(d, parametros)
    assert liq.r(101) == 100_000_000
    assert liq.r(103) == 20_000_000
    assert liq.r(116) == pytest.approx(parametros.impuesto_tabla(20_000_000), abs=1000)


def test_dividendos_sub1_10pct_sobre_exceso_300_uvt(parametros):
    div = parametros.a_pesos(500)  # 500 UVT
    d = DatosDeclaracion(dividendos_sub1=div, patrimonio_bruto=1)
    liq = calcular(d, parametros)
    esperado = round((500 - 300) * 0.10 * parametros.uvt / 1000) * 1000
    assert liq.r(118) == esperado


def test_dividendos_sub2_35pct_mas_tabla(parametros):
    div = 100_000_000
    d = DatosDeclaracion(dividendos_sub2=div, patrimonio_bruto=1)
    liq = calcular(d, parametros)
    parte_a = div * 0.35
    neto_uvt = (div - parte_a) / parametros.uvt
    parte_b = max(0.0, (neto_uvt - 300) * 0.10) * parametros.uvt
    assert liq.r(118) == round((parte_a + parte_b) / 1000) * 1000


# ------------------- ganancias ocasionales ---------------------------------

def test_go_tarifas_general_y_loterias(parametros):
    d = DatosDeclaracion(go_ingresos=100_000_000, go_costos=20_000_000,
                         go_loterias=30_000_000, patrimonio_bruto=1)
    liq = calcular(d, parametros)
    assert liq.r(115) == 80_000_000
    esperado = round((50_000_000 * 0.15 + 30_000_000 * 0.20) / 1000) * 1000
    assert liq.r(127) == esperado


def _liq_go(parametros, *partidas):
    d = DatosDeclaracion(go_partidas=list(partidas), patrimonio_bruto=1)
    return calcular(d, parametros)


def test_go_herencia_vivienda_causante_exenta_hasta_13000_uvt(parametros):
    """Art. 307 num. 1: exentas las primeras 13.000 UVT."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="herencia_vivienda_causante", ingreso=800_000_000))
    exento = 13_000 * parametros.uvt          # 647.387.000
    assert liq.r(114) == exento
    assert liq.r(115) == 800_000_000 - exento
    assert liq.r(127) == round((800_000_000 - exento) * 0.15 / 1000) * 1000


def test_go_no_legitimario_20pct_cuando_el_tope_no_ata(parametros):
    """Art. 307 num. 4: 20% del valor, techo 1.625 UVT (aquí no lo alcanza)."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="herencia_no_legitimario", ingreso=100_000_000))
    assert liq.r(114) == 20_000_000
    assert liq.r(115) == 80_000_000


def test_go_no_legitimario_topado_a_1625_uvt(parametros):
    """El 20% de 500M (=100M) supera el techo, así que manda el techo."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="herencia_no_legitimario", ingreso=500_000_000))
    techo = 1_625 * parametros.uvt            # 80.923.375
    assert liq.r(114) == techo
    assert liq.r(115) == 500_000_000 - techo


def test_go_loteria_ignora_costos_y_no_tiene_exencion(parametros):
    """Art. 317: 20% sobre el bruto, sin costo fiscal ni exención."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="loteria_rifa_apuesta", ingreso=50_000_000, costo_fiscal=999))
    assert liq.r(113) == 0
    assert liq.r(114) == 0
    assert liq.r(115) == 50_000_000
    assert liq.r(127) == 10_000_000


def test_go_vivienda_habitacion_exenta_si_cumple_catastro_y_afc(parametros):
    """Art. 311-1: hasta 5.000 UVT si el catastral no pasa de 15.000 UVT y hay AFC."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="venta_vivienda_habitacion", ingreso=300_000_000,
        valor_catastral=14_000 * parametros.uvt, deposito_afc=True))
    exento = 5_000 * parametros.uvt           # 248.995.000
    assert liq.r(114) == exento
    assert liq.r(127) == round((300_000_000 - exento) * 0.15 / 1000) * 1000


def test_go_vivienda_habitacion_sin_afc_no_exenta(parametros):
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="venta_vivienda_habitacion", ingreso=300_000_000,
        valor_catastral=14_000 * parametros.uvt, deposito_afc=False))
    assert liq.r(114) == 0
    assert liq.r(115) == 300_000_000


def test_go_vivienda_habitacion_catastral_excedido_no_exenta(parametros):
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="venta_vivienda_habitacion", ingreso=300_000_000,
        valor_catastral=16_000 * parametros.uvt, deposito_afc=True))
    assert liq.r(114) == 0


def test_go_venta_activo_fijo_descuenta_costo_sin_exencion(parametros):
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="venta_activo_fijo", ingreso=200_000_000, costo_fiscal=120_000_000))
    assert liq.r(113) == 120_000_000
    assert liq.r(114) == 0
    assert liq.r(115) == 80_000_000
    assert liq.r(127) == 12_000_000


def test_go_mezcla_tarifas_15_y_20(parametros):
    liq = _liq_go(
        parametros,
        GananciaOcasional(tipo="venta_activo_fijo", ingreso=100_000_000,
                          costo_fiscal=40_000_000),          # 60M al 15%
        GananciaOcasional(tipo="loteria_rifa_apuesta", ingreso=25_000_000))  # 25M al 20%
    assert liq.r(115) == 85_000_000
    assert liq.r(127) == round((60_000_000 * 0.15 + 25_000_000 * 0.20) / 1000) * 1000


def test_go_exencion_nunca_supera_la_base_gravable(parametros):
    """Herencia de 10M con tope de 13.000 UVT: se exenta solo lo que hay."""
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="herencia_vivienda_causante", ingreso=10_000_000))
    assert liq.r(114) == 10_000_000
    assert liq.r(115) == 0
    assert liq.r(127) == 0


def test_go_partidas_tipificadas_mandan_sobre_campos_planos(parametros):
    d = DatosDeclaracion(
        go_ingresos=999_000_000, go_loterias=500_000_000,   # deben ignorarse
        go_partidas=[GananciaOcasional(tipo="otra", ingreso=10_000_000)],
        patrimonio_bruto=1)
    liq = calcular(d, parametros)
    assert liq.r(112) == 10_000_000


@pytest.mark.parametrize("partida", [
    GananciaOcasional(tipo="herencia_vivienda_causante", ingreso=800_000_000),
    GananciaOcasional(tipo="herencia_no_legitimario", ingreso=500_000_000),
    GananciaOcasional(tipo="loteria_rifa_apuesta", ingreso=50_000_000),
    GananciaOcasional(tipo="venta_activo_fijo", ingreso=200_000_000,
                      costo_fiscal=120_000_000),
    GananciaOcasional(tipo="seguro_vida", ingreso=200_000_000),
])
def test_go_invariante_r115_es_r112_menos_r113_menos_r114(parametros, partida):
    """La plantilla asume esta identidad en el renglón agregado."""
    liq = _liq_go(parametros, partida)
    assert liq.r(115) == liq.r(112) - liq.r(113) - liq.r(114)


def test_go_tipo_desconocido_cae_en_otra(parametros):
    liq = _liq_go(parametros, GananciaOcasional(
        tipo="inventado_que_no_existe", ingreso=50_000_000))
    assert liq.r(114) == 0
    assert liq.r(127) == round(50_000_000 * 0.15 / 1000) * 1000


def test_go_serializacion_round_trip_conserva_partidas(parametros):
    d = DatosDeclaracion(go_partidas=[
        GananciaOcasional(tipo="seguro_vida", ingreso=200_000_000,
                          descripcion="Póliza de vida")])
    reconstruido = DatosDeclaracion.from_dict(d.to_dict())
    assert reconstruido.go_partidas == d.go_partidas
    assert calcular(reconstruido, parametros).r(115) == calcular(d, parametros).r(115)


def test_go_entrada_plana_no_persiste_partidas_sinteticas():
    """El fallback sintetiza al vuelo; to_dict() no debe inventar partidas."""
    d = DatosDeclaracion(go_ingresos=100_000_000, go_loterias=30_000_000)
    assert len(d.go_partidas_efectivas()) == 2
    assert d.to_dict()["go_partidas"] == []


# ------------------- anticipo y saldos --------------------------------------

def test_anticipo_primer_anio_25pct(parametros):
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=200_000_000),
        aplicar_renta_exenta_25=False, numero_anio_declaracion=1, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    assert liq.r(133) == round(liq.r(126) * 0.25 / 1000) * 1000


def test_anticipo_tercer_anio_promedio(parametros):
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=200_000_000),
        aplicar_renta_exenta_25=False, numero_anio_declaracion=3,
        impuesto_neto_anio_anterior=0, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    promedio = (liq.r(126) + 0) / 2
    assert liq.r(133) == round(promedio * 0.75 / 1000) * 1000


def test_saldo_a_favor(parametros):
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=30_000_000),  # bajo la tabla: impuesto 0
        retenciones=2_000_000, aplicar_renta_exenta_25=False, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    assert liq.r(136) == 0
    assert liq.r(137) == 2_000_000


def test_descuentos_limitados_al_impuesto(parametros):
    # Donación enorme: el descuento por donaciones se capa al 25% del impuesto
    # a cargo (Art. 258 E.T.), no al 100%.
    d = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000),
        descuento_donaciones=999_000_000,
        aplicar_renta_exenta_25=False, patrimonio_bruto=1,
    )
    liq = calcular(d, parametros)
    tope258 = round(liq.r(121) * 0.25 / 1000) * 1000
    assert liq.r(123) == tope258
    assert liq.r(126) == liq.r(121) - tope258
    assert any("258" in a for a in liq.advertencias)

    # Otros descuentos enormes (no donaciones): se capan al impuesto (Art. 259).
    d2 = DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000),
        descuento_dividendos_otros=999_000_000,
        aplicar_renta_exenta_25=False, patrimonio_bruto=1,
    )
    liq2 = calcular(d2, parametros)
    assert liq2.r(125) == liq2.r(121)
    assert liq2.r(126) == 0
    assert any("descuentos" in a.lower() for a in liq2.advertencias)


# ------------------- caso Elizabeth (fixture real, verificado a mano) ------

def test_caso_elizabeth_end_to_end(exogena_elizabeth, parametros):
    """Caso base SIN componente inflacionario (mapeo sin parámetros).

    Verificación manual:
    Ingresos CG = 86.884.216 + 38.242.290 + 644.700 = 125.771.206
    INCRNGO = 3.992.610 → base límite = 121.778.596; 40% = 48.711.438 (< 1.340 UVT)
    Exenta 25% = 25% × (86.884.216 − 3.992.610 − 10.202.142) = 18.172.366
    R37 = 2.810.328 + 7.391.814 + 18.172.366 = 28.374.508 (< límite → no se recorta)
    R91 = 121.779.000 (redondeo a miles)
    R28 = 1% × 8.128.113 = 81.281 → 81.000
    R92 = 28.374.508 + 81.000 = 28.455.508 → R97 = 93.323.492
    Base 93.323.492 / 49.799 = 1.874,0 UVT → rango 28%:
      ((1.874,0 − 1.700) × 0,28 + 116) × 49.799 ≈ 8.203.000
    Anticipo (3er año, promedio): 75% × (8.203.000/2) − 1.363.000 = 1.713.000
    Saldo a pagar = 8.203.000 + 1.713.000 − 1.362.514 = 8.553.486
    """
    datos = mapear_exogena_a_datos(exogena_elizabeth)
    datos.numero_anio_declaracion = 3
    liq = calcular(datos, parametros)

    assert liq.r(29) == 458_522_501
    assert liq.r(30) == 658_958
    assert liq.r(31) == 457_863_543
    assert liq.r(32) == 86_884_216
    assert liq.r(33) == 3_992_610
    assert liq.r(58) == 38_242_290
    assert liq.r(74) == 644_700
    assert liq.r(91) == 121_779_000
    assert liq.r(92) == 28_455_508
    assert liq.r(97) == 93_323_492
    assert liq.r(111) == 93_323_492
    assert liq.r(116) == 8_203_000
    assert liq.r(126) == 8_203_000
    assert liq.r(129) == 8_203_000
    assert liq.r(132) == 1_362_514
    assert liq.r(133) == 1_713_000
    assert liq.r(136) == 8_553_486
    assert liq.r(137) == 0


def test_caso_elizabeth_con_componente_inflacionario(exogena_elizabeth, parametros):
    """Con el % del decreto AG 2025 (55,43%) aplicado a los rendimientos
    financieros que la exógena marca R58|R59 (los 9 rendimientos de CDT):

    Rendimientos CDT = 33.810.314 → R59 = 55,43% = 18.741.057
    R61 = 38.242.290 − 18.741.057 = 19.501.233
    Base límite = 121.778.596 − 18.741.057 = 103.037.539 (40% no recorta)
    R91 = 103.038.000 → R97 = 74.582.492
    Base 74.582.492 / 49.799 = 1.497,7 UVT → rango 19%:
      (1.497,7 − 1.090) × 0,19 × 49.799 ≈ 3.857.000
    Anticipo: 75% × (3.857.000/2) − 1.363.000 = 83.000
    Saldo a pagar = 3.857.000 + 83.000 − 1.362.514 = 2.577.486
    """
    assert parametros.componente_inflacionario == pytest.approx(0.5543)
    datos = mapear_exogena_a_datos(exogena_elizabeth, parametros)
    datos.numero_anio_declaracion = 3

    rendimientos = sum(p.valor for p in exogena_elizabeth.partidas_activas()
                       if p.renglon_asignado == 58 and 59 in p.renglones)
    assert rendimientos == 33_810_314
    assert datos.capital.incrngo == 18_741_057

    liq = calcular(datos, parametros)
    assert liq.r(58) == 38_242_290
    assert liq.r(59) == 18_741_057
    assert liq.r(61) == 19_501_233
    assert liq.r(91) == 103_038_000
    assert liq.r(97) == 74_582_492
    assert liq.r(116) == 3_857_000
    assert liq.r(133) == 83_000
    assert liq.r(136) == 2_577_486
    assert liq.r(137) == 0


def test_componente_inflacionario_no_aplica_a_otros_ingresos_de_capital(exogena_elizabeth, parametros):
    """Los retiros de pensión voluntaria (R58 sin R59) no llevan componente."""
    datos = mapear_exogena_a_datos(exogena_elizabeth, parametros)
    solo_r58 = sum(p.valor for p in exogena_elizabeth.partidas_activas()
                   if p.renglon_asignado == 58 and 59 not in p.renglones)
    assert solo_r58 == 38_242_290 - 33_810_314  # 4.287.643 + 144.333
    # el INCRNGO solo proviene de los rendimientos marcados R58|R59
    assert datos.capital.incrngo == round(33_810_314 * parametros.componente_inflacionario)
