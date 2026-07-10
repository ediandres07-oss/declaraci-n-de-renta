"""Pruebas del Formulario 210 en PDF: campos AcroForm rellenables."""
import pytest
from pypdf import PdfReader

from src.formulario_pdf import generar_formulario_pdf
from src.modelos import DatosDeclaracion, SubcedulaGeneral
from src.motor_calculo import calcular


@pytest.fixture(scope="module")
def datos():
    return DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=180_000_000),
        patrimonio_bruto=500_000_000, deudas=100_000_000,
        retenciones=8_000_000, go_ingresos=50_000_000, go_loterias=50_000_000)


@pytest.fixture(scope="module")
def pdf_rellenable(tmp_path_factory, datos, parametros):
    ruta = tmp_path_factory.mktemp("pdf") / "f210.pdf"
    liq = calcular(datos, parametros)
    generar_formulario_pdf(ruta, datos, liq, parametros)
    return ruta, liq


def test_pdf_trae_campos_acroform(pdf_rellenable):
    ruta, _ = pdf_rellenable
    campos = PdfReader(str(ruta)).get_fields()
    assert campos, "el PDF no expone campos de formulario"
    for renglon in ("R29", "R30", "R31", "R115", "R127", "R133"):
        assert renglon in campos, f"falta el campo {renglon}"


def test_campos_llevan_el_valor_liquidado(pdf_rellenable):
    ruta, liq = pdf_rellenable
    campos = PdfReader(str(ruta)).get_fields()
    # el formulario oficial aproxima al múltiplo de mil
    for renglon in (29, 30, 31, 115, 127):
        esperado = f"{round(liq.r(renglon) / 1000) * 1000:,.0f}"
        assert campos[f"R{renglon}"].get("/V") == esperado


def test_campos_alineados_a_la_derecha(pdf_rellenable):
    """El formulario oficial alinea las cifras a la derecha (/Q 2)."""
    ruta, _ = pdf_rellenable
    lector = PdfReader(str(ruta))
    anotaciones = [a.get_object() for pg in lector.pages
                   for a in (pg.get("/Annots") or [])]
    texto = [a for a in anotaciones if a.get("/FT") == "/Tx"]
    assert texto and all(a.get("/Q") == 2 for a in texto)
    acroform = lector.trailer["/Root"]["/AcroForm"]
    assert acroform.get("/NeedAppearances") == True  # noqa: E712 (BooleanObject de pypdf)


def test_modo_no_rellenable_no_genera_campos(tmp_path, datos, parametros):
    ruta = tmp_path / "estatico.pdf"
    liq = calcular(datos, parametros)
    generar_formulario_pdf(ruta, datos, liq, parametros, rellenable=False)
    assert not PdfReader(str(ruta)).get_fields()


def test_nombres_de_campo_no_se_repiten(pdf_rellenable):
    """Un renglón duplicado haría que el lector sincronice dos casillas distintas."""
    ruta, _ = pdf_rellenable
    lector = PdfReader(str(ruta))
    nombres = [a.get_object().get("/T") for pg in lector.pages
               for a in (pg.get("/Annots") or [])
               if a.get_object().get("/FT") == "/Tx"]
    assert len(nombres) == len(set(nombres))
