"""Integración con WhatsApp Cloud API: parseo, handshake, envío y webhook."""
import pytest

import src.whatsapp as wa
import webapp


CFG_OK = {
    "whatsapp_cloud": {
        "habilitado": True,
        "verify_token": "secreto123",
        "access_token": "TOKEN",
        "phone_number_id": "999",
        "api_version": "v21.0",
    }
}


def _webhook_texto(remitente="573001112233", texto="hola", msg_id="wamid.1"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": remitente, "id": msg_id, "type": "text", "text": {"body": texto}}
    ]}}]}]}


@pytest.fixture(autouse=True)
def _limpiar_memoria():
    wa._hist.clear()
    wa._ids_vistos.clear()
    yield
    wa._hist.clear()
    wa._ids_vistos.clear()


# --- activación y handshake -------------------------------------------------
def test_activo_requiere_token_y_numero():
    assert wa.activo(CFG_OK) is True
    assert wa.activo({"whatsapp_cloud": {"habilitado": True}}) is False
    assert wa.activo({}) is False
    assert wa.activo(None) is False


def test_verificar_webhook():
    assert wa.verificar_webhook(CFG_OK, "subscribe", "secreto123", "reto") == "reto"
    assert wa.verificar_webhook(CFG_OK, "subscribe", "malo", "reto") is None
    assert wa.verificar_webhook(CFG_OK, "otro", "secreto123", "reto") is None
    assert wa.verificar_webhook({}, "subscribe", "", "reto") is None


# --- parseo del payload -----------------------------------------------------
def test_extraer_solo_mensajes_de_texto():
    payload = _webhook_texto(texto="¿cuánto cuesta?")
    assert wa.extraer_mensajes(payload) == [("573001112233", "¿cuánto cuesta?", "wamid.1")]


def test_ignora_callbacks_de_estado_y_no_texto():
    estado = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
    imagen = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "57300", "id": "x", "type": "image"}]}}]}]}
    assert wa.extraer_mensajes(estado) == []
    assert wa.extraer_mensajes(imagen) == []
    assert wa.extraer_mensajes({}) == []
    assert wa.extraer_mensajes(None) == []


# --- atender: responde, recuerda historial y no duplica ---------------------
def test_atender_responde_y_guarda_historial(monkeypatch):
    enviados = []
    monkeypatch.setattr(wa, "enviar", lambda cfg, destino, texto: enviados.append((destino, texto)) or True)

    vistos = {}

    def generar(historial):
        vistos["historial"] = list(historial)
        return "Con gusto: cuesta $79.900."

    n = wa.atender(CFG_OK, _webhook_texto(texto="precio?"), generar)

    assert n == 1
    assert enviados == [("573001112233", "Con gusto: cuesta $79.900.")]
    # el generador recibe el turno del usuario…
    assert vistos["historial"] == [{"rol": "user", "texto": "precio?"}]
    # …y la respuesta queda guardada para el siguiente turno.
    assert wa._hist["573001112233"][-1] == {"rol": "assistant", "texto": "Con gusto: cuesta $79.900."}


def test_atender_no_responde_dos_veces_el_mismo_id(monkeypatch):
    enviados = []
    monkeypatch.setattr(wa, "enviar", lambda *a: enviados.append(a) or True)
    payload = _webhook_texto(msg_id="wamid.dup")

    wa.atender(CFG_OK, payload, lambda h: "hola")
    wa.atender(CFG_OK, payload, lambda h: "hola")   # reintento de Meta

    assert len(enviados) == 1


def test_atender_no_cae_si_el_generador_falla(monkeypatch):
    monkeypatch.setattr(wa, "enviar", lambda *a: True)

    def generar(_):
        raise RuntimeError("Gemini caído")

    assert wa.atender(CFG_OK, _webhook_texto(), generar) == 0


# --- endpoints del webhook --------------------------------------------------
@pytest.fixture()
def cliente():
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


def test_get_verifica_con_token_correcto(cliente, monkeypatch):
    monkeypatch.setattr(webapp, "IA_CFG", CFG_OK)
    r = cliente.get("/api/whatsapp?hub.mode=subscribe&hub.verify_token=secreto123&hub.challenge=42")
    assert r.status_code == 200
    assert r.data == b"42"


def test_get_rechaza_token_malo(cliente, monkeypatch):
    monkeypatch.setattr(webapp, "IA_CFG", CFG_OK)
    r = cliente.get("/api/whatsapp?hub.mode=subscribe&hub.verify_token=malo&hub.challenge=42")
    assert r.status_code == 403


def test_post_siempre_responde_200(cliente, monkeypatch):
    # Sin config activa no intenta responder, pero devuelve 200 igual.
    monkeypatch.setattr(webapp, "IA_CFG", {"whatsapp_cloud": {"habilitado": False}})
    r = cliente.post("/api/whatsapp", json=_webhook_texto())
    assert r.status_code == 200
