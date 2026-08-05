"""Traspaso a humano en WhatsApp (whatsapp.atender)."""
from src import whatsapp as wa


def _payload(remitente, texto, mid):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": remitente, "id": mid, "type": "text", "text": {"body": texto}}]}}]}]}


def test_detecta_pedido_de_humano():
    assert wa._pide_humano("quiero hablar con el dueño")
    assert wa._pide_humano("me escribes cómo te fue")
    assert wa._pide_humano("puedo hablar con una persona")
    assert not wa._pide_humano("cuánto cuesta el pase de temporada")
    assert not wa._pide_humano("qué hace el lector xml dian")


def test_traspaso_avisa_y_pausa(monkeypatch):
    env, avi = [], []
    monkeypatch.setattr(wa, "enviar", lambda cfg, d, t: (env.append((d, t)), True)[1])
    wa._pausados.pop("57300", None)

    n = wa.atender({}, _payload("57300", "quiero hablar con edison", "h1"),
                   lambda h: "respuesta del bot", on_handoff=lambda r, t: avi.append(r))
    assert n == 1
    assert "Edison" in env[0][1]              # mensaje de traspaso, no el del bot
    assert avi == ["57300"]                   # avisó al dueño
    assert wa._en_pausa("57300")

    env.clear()
    n2 = wa.atender({}, _payload("57300", "otra cosa", "h2"),
                    lambda h: "bot", on_handoff=lambda r, t: None)
    assert n2 == 0 and not env               # en pausa: el bot no responde


def test_pregunta_normal_si_responde(monkeypatch):
    env = []
    monkeypatch.setattr(wa, "enviar", lambda cfg, d, t: (env.append((d, t)), True)[1])
    wa._pausados.pop("57999", None)
    n = wa.atender({}, _payload("57999", "cuánto cuesta el pase", "h9"),
                   lambda h: "cuesta 149.900")
    assert n == 1 and env[0][1] == "cuesta 149.900"
