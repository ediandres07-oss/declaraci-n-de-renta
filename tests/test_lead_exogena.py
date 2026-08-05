"""Captura de lead en el cálculo gratis de renta (/api/mi-resultado)."""
import json

from webapp import app
from src.auth import db, OrdenRegistro, LeadExogena


def _limpiar():
    LeadExogena.query.filter(LeadExogena.email.like("leadtest_%")).delete(
        synchronize_session=False)
    OrdenRegistro.query.filter(OrdenRegistro.id.like("leadtest_%")).delete(
        synchronize_session=False)
    db.session.commit()


def test_captura_lead_y_envia_correo(_sin_smtp_real):
    tok = "leadtest_tok1"
    email = "leadtest_persona@test.co"
    with app.app_context():
        _limpiar()
        db.session.add(OrdenRegistro(id=tok, data=json.dumps(
            {"tipo": "carga", "nombre": "JUAN PEREZ", "nit": "1234567890"})))
        db.session.commit()

    r = app.test_client().post("/api/mi-resultado", json={
        "token": tok, "email": " Leadtest_Persona@test.co ", "obligado": True, "valor": 500000})
    assert r.status_code == 200 and r.get_json()["ok"] is True

    with app.app_context():
        lead = db.session.get(LeadExogena, email)          # se normaliza a minúsculas
        assert lead is not None
        assert lead.nombre == "JUAN PEREZ" and lead.nit == "1234567890"
        assert lead.obligado is True and lead.valor == 500000
        _limpiar()

    assert any(m["destino"] == email for m in _sin_smtp_real)      # le llegó su resultado


def test_correo_invalido_no_captura():
    r = app.test_client().post("/api/mi-resultado", json={"token": "x", "email": "nope"})
    assert r.status_code == 400
