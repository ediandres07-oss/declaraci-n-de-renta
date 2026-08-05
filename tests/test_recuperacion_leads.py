"""Secuencia de recuperación de leads del cálculo gratis (gerente.recuperacion_leads)."""
import json
from datetime import date, datetime, timedelta

from webapp import app
from src import gerente
from src.auth import db, LeadExogena, OrdenRegistro


def _lead(email, dias, onboarding="", obligado=False, vence_en=None):
    creado = datetime.utcnow() - timedelta(days=dias)
    fl = (date.today() + timedelta(days=vence_en)) if vence_en is not None else None
    return LeadExogena(email=email, nombre="Ana Gomez", nit="900123456",
                       creado=creado, onboarding=onboarding, obligado=obligado,
                       fecha_limite=fl, valor=500000)


def _limpiar():
    LeadExogena.query.filter(LeadExogena.email.like("rectest_%")).delete(
        synchronize_session=False)
    OrdenRegistro.query.filter(OrdenRegistro.id.like("rectest_%")).delete(
        synchronize_session=False)
    db.session.commit()


def test_recuperacion_envia_el_paso_correcto(_sin_smtp_real):
    with app.app_context():
        _limpiar()
        db.session.add_all([
            _lead("rectest_a@t.co", 1),                          # día 1
            _lead("rectest_b@t.co", 4, onboarding="d1"),         # día 3
            _lead("rectest_c@t.co", 20, obligado=True, vence_en=10),   # ~10 días antes
            _lead("rectest_d@t.co", 1),                          # pagó → skip
            _lead("rectest_e@t.co", 100),                        # viejo, nada
        ])
        db.session.add(OrdenRegistro(id="rectest_ord", data=json.dumps(
            {"tipo": "orden", "estado": "pagada", "email": "rectest_d@t.co"})))
        db.session.commit()

        enviados = gerente.recuperacion_leads()
        destinos = {m["destino"]: m["asunto"] for m in _sin_smtp_real}

        assert enviados == 3
        assert "Guardamos tu resultado" in destinos["rectest_a@t.co"]     # d1
        assert "profesional presenta" in destinos["rectest_b@t.co"]       # d3
        assert "10 días" in destinos["rectest_c@t.co"]                    # venc10
        assert "rectest_d@t.co" not in destinos                          # pagó
        assert "rectest_e@t.co" not in destinos                          # viejo

        a = db.session.get(LeadExogena, "rectest_a@t.co")
        assert "d1" in (a.onboarding or "").split(",")
        _limpiar()
