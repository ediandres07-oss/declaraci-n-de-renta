"""Panel de campañas: segmentación, preview, envío a lista a la medida e historial."""
from webapp import app
from src import gerente
from src.auth import db, CampanaEnviada
from .test_landing import _login_autorizado


def test_segmentacion_lista_excluye_propios():
    with app.app_context():
        d = gerente.destinatarios_campana("lista",
                                          emails=["A@T.co", " b@t.co ", "ediandres07@gmail.com"])
    assert d == ["a@t.co", "b@t.co"]                      # normaliza y excluye el propio


def test_envuelve_mensaje_en_plantilla():
    html = gerente.envolver_campana("<p>Hola</p>", "Ir →", "https://tributando.co")
    assert "Tributando" in html and "Hola" in html and "Ir →" in html


def test_preview_envio_e_historial(_sin_smtp_real):
    c = app.test_client()
    _login_autorizado(c)
    emails = ["camptest_a@t.co", "camptest_b@t.co"]

    pr = c.post("/admin/campana/preview",
                json={"publico": "lista", "emails": ", ".join(emails)}).get_json()
    assert pr["total"] == 2

    j = c.post("/admin/campana/enviar", json={
        "publico": "lista", "emails": emails, "asunto": "CAMPTEST asunto",
        "mensaje": "<p>Prueba</p>", "cta_txt": "Ir", "confirmar": True}).get_json()
    assert j["ok"] and j["enviados"] == 2

    dst = {m["destino"] for m in _sin_smtp_real}
    assert "camptest_a@t.co" in dst and "camptest_b@t.co" in dst

    with app.app_context():
        reg = CampanaEnviada.query.filter_by(asunto="CAMPTEST asunto").first()
        assert reg is not None and reg.enviados == 2 and reg.publico == "lista"
        CampanaEnviada.query.filter_by(asunto="CAMPTEST asunto").delete(synchronize_session=False)
        db.session.commit()

    # sin confirmar → no envía
    r = c.post("/admin/campana/enviar",
               json={"publico": "lista", "emails": emails, "asunto": "x", "mensaje": "y"})
    assert r.status_code == 400
