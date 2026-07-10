"""Pruebas del autollenado del declarante y del resumen ejecutivo en PDF."""
import pytest

from src.entrevista import calcular_dv, mapear_exogena_a_datos, separar_nombre_dian
from src.motor_calculo import calcular
from src.resumen_pdf import generar_resumen_pdf


@pytest.mark.parametrize("nit, dv", [
    ("800197268", "4"),   # DIAN
    ("890903938", "8"),   # Bancolombia
    ("811003890", "4"),   # Global MVM (informante del fixture)
    ("860003020", "1"),   # BBVA
    ("", ""),
])
def test_calcular_dv(nit, dv):
    assert calcular_dv(nit) == dv


@pytest.mark.parametrize("completo, esperado", [
    ("GIRALDO GARCIA ELIZABETH", ("GIRALDO", "GARCIA", "ELIZABETH", "")),
    ("VASQUEZ FLOREZ DIANA ZORANI", ("VASQUEZ", "FLOREZ", "DIANA", "ZORANI")),
    ("PEREZ JUAN", ("PEREZ", "", "JUAN", "")),
    ("SOLOAPELLIDO", ("SOLOAPELLIDO", "", "", "")),
    ("", ("", "", "", "")),
])
def test_separar_nombre_dian(completo, esperado):
    assert separar_nombre_dian(completo) == esperado


def test_declarante_autollenado_desde_exogena(exogena_elizabeth, parametros):
    datos = mapear_exogena_a_datos(exogena_elizabeth, parametros)
    c = datos.contribuyente
    assert c.nit == "44004730"
    assert c.dv == calcular_dv("44004730")
    assert c.primer_apellido == "GIRALDO"
    assert c.segundo_apellido == "GARCIA"
    assert c.primer_nombre == "ELIZABETH"


def test_pdf_resumen_generado(tmp_path, exogena_elizabeth, parametros):
    datos = mapear_exogena_a_datos(exogena_elizabeth, parametros)
    datos.dependientes_detalle = ["Ana"]; datos.dependientes = 1
    liq = calcular(datos, parametros)
    ruta = generar_resumen_pdf(tmp_path / "resumen.pdf", datos, liq, parametros,
                               exogena_elizabeth, ["Patrimonio supera 4.500 UVT"])
    contenido = ruta.read_bytes()
    assert contenido[:5] == b"%PDF-"
    assert len(contenido) > 2000

    # el texto clave debe estar en el PDF
    from pypdf import PdfReader
    texto = "".join(pg.extract_text() for pg in PdfReader(str(ruta)).pages)
    assert "Resumen Ejecutivo" in texto
    assert "44004730" in texto
    assert "SALDO A" in texto


def test_formulario_210_pdf_oficial(tmp_path, exogena_elizabeth, parametros):
    """El PDF estilo oficial trae las casillas, los valores en miles y la marca BORRADOR."""
    from src.formulario_pdf import generar_formulario_pdf
    datos = mapear_exogena_a_datos(exogena_elizabeth, parametros)
    datos.numero_anio_declaracion = 3
    liq = calcular(datos, parametros)
    ruta = generar_formulario_pdf(tmp_path / "f210.pdf", datos, liq, parametros)
    from pypdf import PdfReader
    texto = "".join(pg.extract_text() for pg in PdfReader(str(ruta)).pages)
    assert "BORRADOR" in texto
    assert "Formulario 210" in texto
    assert "GIRALDO" in texto and "ELIZABETH" in texto
    # valores redondeados a miles como el formulario oficial
    assert "86,884,000" in texto      # R32
    assert "18,741,000" in texto      # R59 componente inflacionario
    assert "659,000" in texto         # R30 deudas
    assert "No válido para presentación" in texto
