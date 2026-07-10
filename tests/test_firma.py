"""Pruebas del sello de integridad y la firma PAdES del Formulario 210."""
import json
import datetime

import pytest

from src.firma import (FirmaError, codigo_verificacion, firmar_pdf,
                       sello_integridad, validar_firma, verificar_sello)
from src.formulario_pdf import generar_formulario_pdf, sellar_formulario_pdf
from src.modelos import DatosDeclaracion, SubcedulaGeneral
from src.motor_calculo import calcular

CLAVE = "clave-de-prueba"


@pytest.fixture(scope="module")
def certificado_p12():
    """Certificado autofirmado en memoria: nunca toca el disco."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Contador Prueba")])
    desde = datetime.datetime(2026, 1, 1)
    cert = (x509.CertificateBuilder()
            .subject_name(nombre).issuer_name(nombre)
            .public_key(llave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(desde)
            .not_valid_after(desde + datetime.timedelta(days=365))
            .sign(llave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(
        b"prueba", llave, cert, None,
        serialization.BestAvailableEncryption(CLAVE.encode()))


@pytest.fixture
def pdf(tmp_path, parametros):
    datos = DatosDeclaracion(trabajo=SubcedulaGeneral(ingresos_brutos=180_000_000),
                             patrimonio_bruto=500_000_000, deudas=100_000_000)
    ruta = tmp_path / "f210.pdf"
    generar_formulario_pdf(ruta, datos, calcular(datos, parametros), parametros)
    return ruta


# ------------------- sello de integridad -----------------------------------

def test_sello_detecta_alteracion(pdf):
    sello = sello_integridad(pdf)
    assert verificar_sello(pdf, sello)
    pdf.write_bytes(pdf.read_bytes() + b"% alterado")
    assert not verificar_sello(pdf, sello)


def test_codigo_de_verificacion_es_estable_y_legible(pdf):
    codigo = codigo_verificacion(pdf)
    assert codigo == codigo_verificacion(pdf)
    assert len(codigo) == 19 and codigo.count("-") == 3


def test_sellar_imprime_el_codigo_y_conserva_los_campos(pdf):
    from pypdf import PdfReader
    antes = PdfReader(str(pdf)).get_fields()
    codigo = sellar_formulario_pdf(pdf)
    lector = PdfReader(str(pdf))
    assert codigo in lector.pages[0].extract_text()
    despues = lector.get_fields()
    assert set(despues) == set(antes)
    assert despues["R29"]["/V"] == antes["R29"]["/V"]


# ------------------- firma PAdES -------------------------------------------

def test_firmar_produce_pdf_valido_e_integro(pdf, certificado_p12):
    firmado = firmar_pdf(pdf, certificado_p12, CLAVE)
    estado = validar_firma(firmado)
    assert estado["firmado"] and estado["integro"]
    assert "Contador Prueba" in estado["firmante"]


def test_firma_detecta_pdf_alterado(pdf, certificado_p12):
    """Cambiar el patrimonio bruto del PDF firmado debe romper la integridad."""
    import re

    firmado = firmar_pdf(pdf, certificado_p12, CLAVE)
    crudo = bytearray(firmado.read_bytes())
    # el valor de R29 se guarda como cadena PDF con las comas escapadas: (500\054000\054000)
    m = re.search(rb"/V \((5[^)]*)\)", bytes(crudo))
    assert m, "no se encontró el valor de R29 en el PDF firmado"
    crudo[m.start(1)] = ord("9")     # 500.000.000 -> 900.000.000, mismo tamaño
    firmado.write_bytes(bytes(crudo))
    assert validar_firma(firmado)["integro"] is False


def test_firmar_conserva_los_campos_rellenables(pdf, certificado_p12):
    from pypdf import PdfReader
    antes = set(PdfReader(str(pdf)).get_fields())
    firmado = firmar_pdf(pdf, certificado_p12, CLAVE)
    despues = set(PdfReader(str(firmado)).get_fields())
    assert antes <= despues          # se suma el campo de firma


def test_pdf_sin_firma_se_reporta_como_no_firmado(pdf):
    estado = validar_firma(pdf)
    assert estado == {"firmado": False, "integro": False, "firmante": "", "razon": ""}


def test_contrasena_incorrecta_da_error_claro(pdf, certificado_p12):
    with pytest.raises(FirmaError, match="contraseña"):
        firmar_pdf(pdf, certificado_p12, "clave-equivocada")


def test_certificado_corrupto_da_error_claro(pdf):
    with pytest.raises(FirmaError):
        firmar_pdf(pdf, b"esto no es un p12", CLAVE)


def test_flujo_sellar_luego_firmar(pdf, certificado_p12):
    """El sello reescribe el PDF, así que debe ir antes de la firma."""
    sellar_formulario_pdf(pdf)
    firmado = firmar_pdf(pdf, certificado_p12, CLAVE)
    assert validar_firma(firmado)["integro"]


# ------------------- endpoints web ------------------------------------------

@pytest.fixture()
def cliente_autorizado():
    from webapp import app
    from src.auth import Usuario, _correos_autorizados, db

    app.config["TESTING"] = True
    email = next(iter(_correos_autorizados()), "staff@test.com")
    with app.app_context():
        u = Usuario.query.filter_by(email=email).first()
        if u is None:
            u = Usuario(proveedor="google", proveedor_id="staff-firma",
                        email=email, nombre="Staff")
            db.session.add(u)
            db.session.commit()
        elif u.proveedor not in ("google", "microsoft"):
            u.proveedor = "google"
            db.session.commit()
        uid = u.id
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["uid"] = uid
        yield c


_DATOS = {"trabajo": {"ingresos_brutos": 180_000_000}, "patrimonio_bruto": 500_000_000}


def test_endpoint_formulario_pdf_sella_el_documento(cliente_autorizado):
    import io

    from pypdf import PdfReader

    r = cliente_autorizado.post("/api/formulario-pdf", json={"datos": _DATOS})
    assert r.status_code == 200
    lector = PdfReader(io.BytesIO(r.data))
    assert "Código de verificación" in lector.pages[0].extract_text()
    assert lector.get_fields()          # sigue siendo rellenable


def test_endpoint_firmar_pdf_devuelve_pdf_firmado(cliente_autorizado, certificado_p12, tmp_path):
    import io

    r = cliente_autorizado.post("/api/firmar-pdf", data={
        "datos": json.dumps(_DATOS),
        "passphrase": CLAVE,
        "certificado": (io.BytesIO(certificado_p12), "cert.p12"),
    }, content_type="multipart/form-data")
    assert r.status_code == 200

    ruta = tmp_path / "descargado.pdf"
    ruta.write_bytes(r.data)
    estado = validar_firma(ruta)
    assert estado["firmado"] and estado["integro"]
    assert "Contador Prueba" in estado["firmante"]


def test_endpoint_firmar_pdf_con_clave_mala_da_400(cliente_autorizado, certificado_p12):
    import io

    r = cliente_autorizado.post("/api/firmar-pdf", data={
        "datos": json.dumps(_DATOS),
        "passphrase": "no-es-la-clave",
        "certificado": (io.BytesIO(certificado_p12), "cert.p12"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400
    assert "contraseña" in r.get_json()["error"]


def test_endpoint_firmar_pdf_sin_certificado_da_400(cliente_autorizado):
    r = cliente_autorizado.post("/api/firmar-pdf", data={"datos": json.dumps(_DATOS)},
                                content_type="multipart/form-data")
    assert r.status_code == 400
