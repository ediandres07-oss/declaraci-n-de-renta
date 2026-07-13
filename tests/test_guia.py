"""Lead magnet: captura de correo (/api/guia) y lista de espera."""
import json

import pytest

import webapp


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "LISTA_ESPERA_PATH", tmp_path / "lista.json")
    webapp._chat_ips.clear()          # aísla el antiabuso por IP entre tests
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def test_correo_valido_guarda_y_devuelve_pdf(cliente):
    r = cliente.post("/api/guia", json={"email": "Ana@Ejemplo.com", "nombre": "Ana"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["url"].endswith(".pdf")
    lista = json.load(open(webapp.LISTA_ESPERA_PATH))
    assert lista[0]["email"] == "ana@ejemplo.com"      # se normaliza a minúsculas
    assert lista[0]["nombre"] == "Ana" and "fecha" in lista[0]


def test_correo_invalido_no_guarda(cliente):
    r = cliente.post("/api/guia", json={"email": "no-es-correo"})
    assert r.status_code == 400
    assert not webapp.LISTA_ESPERA_PATH.exists()


def test_no_duplica_el_mismo_correo(cliente):
    cliente.post("/api/guia", json={"email": "x@y.com"})
    cliente.post("/api/guia", json={"email": "x@y.com"})
    assert len(json.load(open(webapp.LISTA_ESPERA_PATH))) == 1


def test_el_pdf_de_la_guia_existe_y_es_valido():
    """El archivo servido debe existir en static/ y ser un PDF de verdad."""
    ruta = webapp.BASE / "static" / webapp.GUIA_ARCHIVO
    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"
