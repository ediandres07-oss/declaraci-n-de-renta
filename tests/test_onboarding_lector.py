"""Secuencia de onboarding del trial del Lector (gerente.onboarding_lector)."""
from datetime import date, datetime, timedelta

from webapp import app
from src import gerente
from src.auth import db, SuscripcionLector


def _trial(lic, dias_desde_creado, onboarding="", plan="prueba", vence_en=None):
    creado = datetime.utcnow() - timedelta(days=dias_desde_creado)
    vence = (date.today() + timedelta(days=vence_en)) if vence_en is not None else None
    return SuscripcionLector(licencia=lic, email=f"{lic}@test.co", plan=plan,
                             activa=True, creado=creado, vence=vence,
                             onboarding=onboarding)


def test_onboarding_envia_el_correo_del_dia(_sin_smtp_real):
    pref = "obtest_"
    with app.app_context():
        SuscripcionLector.query.filter(
            SuscripcionLector.licencia.like(f"{pref}%")).delete(synchronize_session=False)
        db.session.commit()
        db.session.add_all([
            _trial(pref + "a_dia1", 1, vence_en=29),      # día 1 → sí
            _trial(pref + "b_dia7", 7),                    # día 7 → sí
            _trial(pref + "c_vieja", 100),                 # vieja → nada
            _trial(pref + "d_dedup", 1, onboarding="1"),   # ya enviado día 1 → nada
            _trial(pref + "e_pagado_prueba", 20, vence_en=8),
        ])
        # 'e' también tiene un plan pagado activo con el mismo correo → no molestar
        db.session.add(SuscripcionLector(
            licencia=pref + "e_pagado", email=pref + "e_pagado_prueba@test.co",
            plan="pro_mensual", activa=True, creado=datetime.utcnow()))
        db.session.commit()

        enviados = gerente.onboarding_lector()

        destinos = {m["destino"]: m["asunto"] for m in _sin_smtp_real}
        assert enviados == 2
        assert pref + "a_dia1@test.co" in destinos
        assert "3 pasos" in destinos[pref + "a_dia1@test.co"]          # día 1
        assert "Lo que hacen" in destinos[pref + "b_dia7@test.co"]     # día 7
        assert pref + "c_vieja@test.co" not in destinos                # vieja
        assert pref + "d_dedup@test.co" not in destinos                # dedup
        assert pref + "e_pagado_prueba@test.co" not in destinos        # ya paga

        # se marcó lo enviado (no se repetirá mañana)
        a = db.session.get(SuscripcionLector, pref + "a_dia1")
        assert "1" in (a.onboarding or "").split(",")

        # limpieza
        SuscripcionLector.query.filter(
            SuscripcionLector.licencia.like(f"{pref}%")).delete(synchronize_session=False)
        db.session.commit()


def test_correos_tienen_cta_correcto():
    class _S:
        email = "x@test.co"
        vence = date.today() + timedelta(days=10)
    for paso, url in [(1, "/descargar-lector"), (7, "/descargar-lector"),
                      (20, "/contadores/lector"), (28, "/contadores/lector")]:
        asunto, html = gerente._correo_onboarding(paso, _S())
        assert asunto and url in html
