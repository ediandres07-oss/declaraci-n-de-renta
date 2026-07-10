"""Segundo factor (TOTP): activación, verificación, códigos de respaldo y bloqueo."""
import json

import pyotp
import pytest

from src.auth import MAX_INTENTOS_MFA, Usuario, UsuarioMFA, db
from webapp import app


@pytest.fixture()
def usuario():
    """Un usuario limpio, sin 2FA, en la BD de pruebas."""
    app.config["TESTING"] = True
    with app.app_context():
        u = Usuario.query.filter_by(email="mfa@test.local").first()
        if u is not None:
            if u.mfa:
                db.session.delete(u.mfa)
            db.session.delete(u)
            db.session.commit()
        u = Usuario(proveedor="google", proveedor_id="mfa-test",
                    email="mfa@test.local", nombre="Prueba MFA")
        db.session.add(u)
        db.session.commit()
        uid = u.id
    yield uid
    with app.app_context():
        u = db.session.get(Usuario, uid)
        if u:
            if u.mfa:
                db.session.delete(u.mfa)
            db.session.delete(u)
            db.session.commit()


@pytest.fixture()
def cliente(usuario):
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["uid"] = usuario
        yield c


def _activar_2fa(cliente):
    """Recorre el flujo real de activación y devuelve (secreto, backup_codes)."""
    secreto = cliente.post("/api/configurar-2fa").get_json()["secreto"]
    r = cliente.post("/api/confirmar-2fa",
                     json={"codigo": pyotp.TOTP(secreto).now()})
    assert r.status_code == 200
    return secreto, r.get_json()["backup_codes"]


# ------------------- activación --------------------------------------------

def test_configurar_devuelve_qr_y_no_activa_nada_todavia(cliente, usuario):
    j = cliente.post("/api/configurar-2fa").get_json()
    assert j["qr"].startswith("data:image/png;base64,")
    assert len(j["secreto"]) >= 16
    with app.app_context():
        assert not db.session.get(Usuario, usuario).mfa_habilitado


def test_confirmar_con_codigo_malo_no_activa(cliente, usuario):
    cliente.post("/api/configurar-2fa")
    r = cliente.post("/api/confirmar-2fa", json={"codigo": "000000"})
    assert r.status_code == 401
    with app.app_context():
        assert not db.session.get(Usuario, usuario).mfa_habilitado


def test_confirmar_con_codigo_valido_activa_y_entrega_backup_codes(cliente, usuario):
    _, codigos = _activar_2fa(cliente)
    assert len(codigos) == 10
    with app.app_context():
        u = db.session.get(Usuario, usuario)
        assert u.mfa_habilitado and u.mfa.totp_habilitado
        # los códigos se guardan hasheados, nunca en claro
        guardados = json.loads(u.mfa.backup_hashes)
        assert len(guardados) == 10
        assert not any(c in u.mfa.backup_hashes for c in codigos)


# ------------------- el 2FA realmente bloquea la sesión ---------------------

def test_login_con_2fa_no_abre_sesion_hasta_verificar(cliente, usuario):
    """Lo esencial: pasar OAuth deja al usuario 'pendiente', sin sesión."""
    _activar_2fa(cliente)
    with cliente.session_transaction() as s:
        s.clear()
        s["uid_pendiente"] = usuario        # estado tras el callback de OAuth

    # una ruta protegida debe rechazarlo: aún no tiene sesión
    assert cliente.post("/api/chat", json={"mensajes": [{"rol": "user", "texto": "hola"}]}
                        ).status_code == 401
    assert cliente.get("/verificar-mfa").status_code == 200


def test_codigo_totp_valido_promueve_la_sesion(cliente, usuario):
    secreto, _ = _activar_2fa(cliente)
    with cliente.session_transaction() as s:
        s.clear()
        s["uid_pendiente"] = usuario

    r = cliente.post("/api/verificar-codigo-totp",
                     json={"codigo": pyotp.TOTP(secreto).now()})
    assert r.status_code == 200
    with cliente.session_transaction() as s:
        assert s["uid"] == usuario
        assert "uid_pendiente" not in s


def test_verificar_sin_verificacion_en_curso_da_401(cliente):
    with cliente.session_transaction() as s:
        s.clear()
    assert cliente.post("/api/verificar-codigo-totp", json={"codigo": "123456"}
                        ).status_code == 401


# ------------------- códigos de respaldo ------------------------------------

def test_backup_code_sirve_una_sola_vez(cliente, usuario):
    _, codigos = _activar_2fa(cliente)
    codigo = codigos[0]

    for esperado in (200, 401):              # el segundo intento ya no vale
        with cliente.session_transaction() as s:
            s.clear()
            s["uid_pendiente"] = usuario
        r = cliente.post("/api/verificar-codigo-totp", json={"codigo": codigo})
        assert r.status_code == esperado

    with app.app_context():
        restantes = json.loads(db.session.get(Usuario, usuario).mfa.backup_hashes)
        assert len(restantes) == 9


# ------------------- bloqueo por intentos -----------------------------------

def test_bloqueo_tras_intentos_fallidos(cliente, usuario):
    _activar_2fa(cliente)
    with cliente.session_transaction() as s:
        s.clear()
        s["uid_pendiente"] = usuario

    for _ in range(MAX_INTENTOS_MFA):
        assert cliente.post("/api/verificar-codigo-totp",
                            json={"codigo": "000000"}).status_code == 401

    r = cliente.post("/api/verificar-codigo-totp", json={"codigo": "000000"})
    assert r.status_code == 429
    assert "intentos" in r.get_json()["error"].lower()


def test_bloqueo_rechaza_incluso_el_codigo_correcto(cliente, usuario):
    secreto, _ = _activar_2fa(cliente)
    with cliente.session_transaction() as s:
        s.clear()
        s["uid_pendiente"] = usuario
    for _ in range(MAX_INTENTOS_MFA):
        cliente.post("/api/verificar-codigo-totp", json={"codigo": "000000"})

    r = cliente.post("/api/verificar-codigo-totp",
                     json={"codigo": pyotp.TOTP(secreto).now()})
    assert r.status_code == 429


# ------------------- desactivación ------------------------------------------

def test_desactivar_exige_codigo_valido(cliente, usuario):
    secreto, _ = _activar_2fa(cliente)

    assert cliente.post("/api/desactivar-2fa", json={"codigo": "000000"}
                        ).status_code == 401
    with app.app_context():
        assert db.session.get(Usuario, usuario).mfa_habilitado

    assert cliente.post("/api/desactivar-2fa",
                        json={"codigo": pyotp.TOTP(secreto).now()}).status_code == 200
    with app.app_context():
        u = db.session.get(Usuario, usuario)
        assert not u.mfa_habilitado and u.mfa is None
