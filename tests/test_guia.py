"""Lead magnet: captura de correo (/api/guia) y lista de espera en la BD."""
import pytest

import webapp
from src.auth import LeadEspera, db


@pytest.fixture()
def cliente():
    webapp._chat_ips.clear()          # aísla el antiabuso por IP entre tests
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def _limpiar(email):
    with webapp.app.app_context():
        LeadEspera.query.filter_by(email=email).delete()
        db.session.commit()


def test_correo_valido_guarda_y_devuelve_pdf(cliente):
    email = "ana.guia@ejemplo.com"
    _limpiar(email)
    r = cliente.post("/api/guia", json={"email": "Ana.Guia@Ejemplo.com", "nombre": "Ana"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["url"].endswith(".pdf")
    with webapp.app.app_context():
        fila = LeadEspera.query.filter_by(email=email).first()   # se normaliza a minúsculas
        assert fila is not None and fila.nombre == "Ana"
    _limpiar(email)


def test_correo_invalido_no_guarda(cliente):
    email = "no-es-correo"
    r = cliente.post("/api/guia", json={"email": email})
    assert r.status_code == 400
    with webapp.app.app_context():
        assert LeadEspera.query.filter_by(email=email).first() is None


def test_no_duplica_el_mismo_correo(cliente):
    email = "dup.guia@ejemplo.com"
    _limpiar(email)
    cliente.post("/api/guia", json={"email": email})
    cliente.post("/api/guia", json={"email": email})
    with webapp.app.app_context():
        assert LeadEspera.query.filter_by(email=email).count() == 1
    _limpiar(email)


def test_el_pdf_de_la_guia_existe_y_es_valido():
    """El archivo servido debe existir en static/ y ser un PDF de verdad."""
    ruta = webapp.BASE / "static" / webapp.GUIA_ARCHIVO
    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"
