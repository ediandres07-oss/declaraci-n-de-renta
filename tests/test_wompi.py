"""Integración Wompi: firma de integridad, URL de checkout, retorno y webhook."""
import hashlib

import pytest

from webapp import app
import webapp as w
from src import wompi
from .conftest import EXOGENA


CFG = {"habilitado": True, "moneda": "COP", "public_key": "pub_test_ABC",
       "private_key": "prv_test_XYZ", "integrity_secret": "int_secret",
       "events_secret": "evt_secret"}


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    from src.auth import OrdenRegistro, db
    with app.app_context():           # órdenes ahora viven en la BD: tabla limpia por test
        OrdenRegistro.query.delete()
        db.session.commit()
    monkeypatch.setattr(w, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(w, "CLIENTES_DIR", tmp_path / "clientes")
    monkeypatch.setattr(w, "WOMPI", CFG)
    app.config["TESTING"] = True
    with app.test_client() as c:
        _login(c)
        yield c


def _login(cliente):
    from src.auth import db, Usuario
    with app.app_context():
        u = Usuario.query.filter_by(email="cli@test.com").first()
        if u is None:
            u = Usuario(proveedor="google", proveedor_id="cli", email="cli@test.com", nombre="Cli")
            db.session.add(u); db.session.commit()
        uid = u.id
    with cliente.session_transaction() as s:
        s["uid"] = uid


def _crear_orden(cliente, plan="pdf"):
    with open(EXOGENA, "rb") as fh:
        j = cliente.post("/api/cargar-landing", data={"exogena": (fh, "e.xlsx")},
                         content_type="multipart/form-data").get_json()
    return cliente.post("/api/checkout", json={
        "token": j["token"], "plan": plan, "contacto": {"email": "cli@test.com"}}).get_json()["orden_id"]


# --------------------------------------------------------- unidad
def test_firma_integridad_coincide_con_sha256():
    esperado = hashlib.sha256("RENTA-abc1234599900COPint_secret".encode()).hexdigest()
    assert wompi.firma_integridad("RENTA-abc12345", 99900, "COP", "int_secret") == esperado


def test_url_checkout_incluye_parametros():
    url = wompi.url_checkout(CFG, "RENTA-x", 7990000, "http://localhost/ret", "a@b.co")
    assert url.startswith("https://checkout.wompi.co/p/?")
    assert "public-key=pub_test_ABC" in url
    assert "amount-in-cents=7990000" in url
    assert "reference=RENTA-x" in url
    assert "signature%3Aintegrity=" in url          # ':' url-encodeado


def test_activo():
    assert wompi.activo(CFG) is True
    assert wompi.activo({**CFG, "habilitado": False}) is False
    assert wompi.activo({**CFG, "integrity_secret": ""}) is False


def test_validar_firma_evento():
    # checksum = SHA256(id + status + timestamp + secret)
    data = {"transaction": {"id": "T1", "status": "APPROVED"}}
    cadena = "T1APPROVED" + "1700000000" + "evt_secret"
    checksum = hashlib.sha256(cadena.encode()).hexdigest()
    evento = {"data": data, "timestamp": "1700000000",
              "signature": {"properties": ["transaction.id", "transaction.status"], "checksum": checksum}}
    assert wompi.validar_firma_evento(CFG, evento) is True
    evento["signature"]["checksum"] = "malo"
    assert wompi.validar_firma_evento(CFG, evento) is False


# --------------------------------------------------------- endpoints
def test_checkout_wompi_devuelve_url(cliente):
    oid = _crear_orden(cliente)
    r = cliente.post("/api/checkout-wompi", json={"orden_id": oid})
    assert r.status_code == 200
    url = r.get_json()["url"]
    assert url.startswith("https://checkout.wompi.co/p/?")
    assert f"reference=RENTA-{oid}" in url


def test_checkout_wompi_deshabilitado(cliente, monkeypatch):
    monkeypatch.setattr(w, "WOMPI", {"habilitado": False})
    r = cliente.post("/api/checkout-wompi", json={"orden_id": "x"})
    assert r.status_code == 400


def test_retorno_aprobado_marca_pagada(cliente, monkeypatch):
    oid = _crear_orden(cliente)
    monkeypatch.setattr(w.wompi_mod, "consultar_transaccion",
                        lambda cfg, tx: {"status": "APPROVED", "reference": f"RENTA-{oid}"})
    r = cliente.get(f"/pago/wompi/retorno?id=TX123")
    assert r.status_code == 200 and "aprobado" in r.data.decode().lower()
    assert w._leer_ordenes()[oid]["estado"] == "pagada"


def test_retorno_rechazado(cliente, monkeypatch):
    oid = _crear_orden(cliente)
    monkeypatch.setattr(w.wompi_mod, "consultar_transaccion",
                        lambda cfg, tx: {"status": "DECLINED", "reference": f"RENTA-{oid}"})
    cliente.get("/pago/wompi/retorno?id=TX9")
    assert w._leer_ordenes()[oid]["estado"] == "pago_fallido"


def test_webhook_aprobado_marca_pagada(cliente):
    oid = _crear_orden(cliente, plan="presentacion")
    tx = {"id": "TX5", "status": "APPROVED", "reference": f"RENTA-{oid}"}
    cadena = f"TX5APPROVED1700000000evt_secret"
    checksum = hashlib.sha256(cadena.encode()).hexdigest()
    evento = {"data": {"transaction": tx}, "timestamp": "1700000000",
              "signature": {"properties": ["transaction.id", "transaction.status"], "checksum": checksum}}
    r = cliente.post("/api/wompi-webhook", json=evento)
    assert r.status_code == 200
    assert w._leer_ordenes()[oid]["estado"] == "pagada_en_tramite"


def test_webhook_firma_invalida_rechaza(cliente):
    evento = {"data": {"transaction": {"id": "T", "status": "APPROVED", "reference": "RENTA-x"}},
              "timestamp": "1", "signature": {"properties": ["transaction.id"], "checksum": "malo"}}
    assert cliente.post("/api/wompi-webhook", json=evento).status_code == 403
