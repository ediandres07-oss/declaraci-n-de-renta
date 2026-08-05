"""Métricas del panel /admin/dashboard (gerente.metricas_negocio)."""
import json
from datetime import date, datetime, timedelta

from webapp import app
from src import gerente
from src.auth import db, OrdenRegistro, SuscripcionLector


def _orden(oid, plan, estado, precio):
    return OrdenRegistro(id=oid, data=json.dumps(
        {"tipo": "orden", "plan": plan, "estado": estado, "precio": precio}))


def test_metricas_cuentan_por_tipo_y_estado():
    pref = "dashtest_"
    with app.app_context():
        OrdenRegistro.query.filter(OrdenRegistro.id.like(f"{pref}%")).delete(
            synchronize_session=False)
        SuscripcionLector.query.filter(SuscripcionLector.licencia.like(f"{pref}%")).delete(
            synchronize_session=False)
        db.session.commit()

        antes = gerente.metricas_negocio()

        db.session.add_all([
            _orden(pref + "1", "presentacion", "pagada", 149900),   # B2C pagada
            _orden(pref + "2", "pdf", "pendiente", 49900),          # B2C creada, no pagada
            _orden(pref + "3", "contadores", "pagada", 149900),     # pase vendido
            _orden(pref + "4", "lector", "pagada", 60000),          # suscripción Lector
            SuscripcionLector(licencia=pref + "p", email=pref + "p@t.co",
                              plan="prueba", activa=True, creado=datetime.utcnow(),
                              vence=date.today() + timedelta(days=20)),
        ])
        db.session.commit()

        d = gerente.metricas_negocio()
        assert d["b2c_creadas"] == antes["b2c_creadas"] + 2
        assert d["b2c_pagadas"] == antes["b2c_pagadas"] + 1
        assert d["b2c_ingreso"] == antes["b2c_ingreso"] + 149900
        assert d["pase_pagadas"] == antes["pase_pagadas"] + 1
        assert d["pase_ingreso"] == antes["pase_ingreso"] + 149900
        assert d["lector_pagadas_activas"] == antes["lector_pagadas_activas"] + 1
        assert d["lector_pruebas_activas"] == antes["lector_pruebas_activas"] + 1

        OrdenRegistro.query.filter(OrdenRegistro.id.like(f"{pref}%")).delete(
            synchronize_session=False)
        SuscripcionLector.query.filter(SuscripcionLector.licencia.like(f"{pref}%")).delete(
            synchronize_session=False)
        db.session.commit()
