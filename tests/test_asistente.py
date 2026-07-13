"""Asistente de IA (chat web): activación, prompt, normalización y endpoint."""
import types

import pytest

from webapp import app
import src.asistente as asistente


@pytest.fixture()
def cliente():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _login_cliente(cliente):
    """Inicia sesión con un usuario CUALQUIERA (no personal autorizado) —
    sirve para probar que el chat conoce al usuario cuando SÍ hay sesión."""
    from src.auth import db, Usuario
    with app.app_context():
        u = Usuario.query.filter_by(email="cliente@test.com").first()
        if u is None:
            u = Usuario(proveedor="google", proveedor_id="cli1", email="cliente@test.com", nombre="Cliente")
            db.session.add(u)
            db.session.commit()
        uid = u.id
    with cliente.session_transaction() as s:
        s["uid"] = uid


def _cfg_activa():
    return {"habilitado": True, "api_key": "AIza-prueba", "modelo": "gemini-2.0-flash",
            "nombre_asistente": "Asistente", "negocio": {"nombre": "Renta", "correo": "x@y.co"}}


def _mock_gemini(monkeypatch, captura):
    """Reemplaza google.genai por un doble que registra la llamada."""
    class _Modelos:
        def generate_content(self, **kw):
            captura.update(kw)
            return types.SimpleNamespace(text="Respuesta de prueba.")

    class _Cliente:
        def __init__(self, api_key=None):
            captura["api_key"] = api_key
            self.models = _Modelos()

    def _config(**kw):
        captura["config_kwargs"] = kw
        return types.SimpleNamespace(**kw)

    types_mod = types.SimpleNamespace(GenerateContentConfig=_config)
    # 'types' expuesto como atributo para que `from google.genai import types` funcione
    genai_mod = types.SimpleNamespace(Client=_Cliente, types=types_mod)
    google_mod = types.SimpleNamespace(genai=genai_mod)
    sysmod = __import__("sys").modules
    monkeypatch.setitem(sysmod, "google", google_mod)
    monkeypatch.setitem(sysmod, "google.genai", genai_mod)
    monkeypatch.setitem(sysmod, "google.genai.types", types_mod)


def test_activo_requiere_habilitado_y_key():
    assert asistente.asistente_activo({"habilitado": True, "api_key": ""}) is False
    assert asistente.asistente_activo({"habilitado": False, "api_key": "sk"}) is False
    assert asistente.asistente_activo({"habilitado": True, "api_key": "sk"}) is True


def test_prompt_incluye_datos_del_servicio():
    p = asistente._prompt_sistema(_cfg_activa())
    assert "79.900" in p and "189.900" in p        # precios de los planes
    assert "Formulario 210" in p and "exógena" in p
    assert "4.500 UVT" in p and "1.400 UVT" in p    # topes de obligación


def test_responder_arma_bien_la_llamada(monkeypatch):
    captura = {}
    _mock_gemini(monkeypatch, captura)
    hist = [{"rol": "user", "texto": "  ¿Cuánto cuesta?  "},
            {"rol": "assistant", "texto": "Hay dos planes."},
            {"rol": "user", "texto": "¿Y presentan en la DIAN?"}]
    r = asistente.responder(hist, _cfg_activa())
    assert r == "Respuesta de prueba."
    assert captura["api_key"] == "AIza-prueba"
    assert captura["model"] == "gemini-2.0-flash"
    # historial normalizado a roles Gemini user/model y texto recortado
    assert [m["role"] for m in captura["contents"]] == ["user", "model", "user"]
    assert captura["contents"][0]["parts"][0]["text"] == "¿Cuánto cuesta?"
    # el prompt del servicio viaja como system_instruction
    assert "79.900" in captura["config_kwargs"]["system_instruction"]


def test_responder_rechaza_si_no_empieza_en_usuario(monkeypatch):
    _mock_gemini(monkeypatch, {})
    with pytest.raises(ValueError):
        asistente.responder([{"rol": "assistant", "texto": "hola"}], _cfg_activa())


def test_responder_falla_si_inactivo():
    with pytest.raises(RuntimeError):
        asistente.responder([{"rol": "user", "texto": "hola"}],
                            {"habilitado": False, "api_key": ""})


def test_chat_no_exige_login(cliente, monkeypatch):
    """El chat es el primer contacto de un cliente potencial: responde sin sesión."""
    captura = {}
    _mock_gemini(monkeypatch, captura)
    monkeypatch.setattr("webapp.IA_CFG", _cfg_activa())
    monkeypatch.setattr("webapp._chat_ips", {})
    r = cliente.post("/api/chat", json={"mensajes": [{"rol": "user", "texto": "hola"}]})
    assert r.status_code == 200
    assert r.get_json()["respuesta"] == "Respuesta de prueba."


def test_chat_limita_mensajes_por_ip(cliente, monkeypatch):
    """Sin login el abuso se frena con un límite de mensajes por IP (429)."""
    _mock_gemini(monkeypatch, {})
    monkeypatch.setattr("webapp.IA_CFG", _cfg_activa())
    monkeypatch.setattr("webapp._chat_ips", {})
    monkeypatch.setattr("webapp._CHAT_MAX_POR_IP", 2)
    cuerpo = {"mensajes": [{"rol": "user", "texto": "hola"}]}
    assert cliente.post("/api/chat", json=cuerpo).status_code == 200
    assert cliente.post("/api/chat", json=cuerpo).status_code == 200
    r = cliente.post("/api/chat", json=cuerpo)
    assert r.status_code == 429
    assert "muchos mensajes" in r.get_json()["error"]


def test_endpoint_chat_desactivado(cliente, monkeypatch):
    monkeypatch.setattr("webapp.IA_CFG", {"habilitado": False, "api_key": ""})
    r = cliente.post("/api/chat", json={"mensajes": [{"rol": "user", "texto": "hola"}]})
    assert r.status_code == 503


def test_endpoint_chat_activo(cliente, monkeypatch):
    _login_cliente(cliente)
    captura = {}
    _mock_gemini(monkeypatch, captura)
    monkeypatch.setattr("webapp.IA_CFG", _cfg_activa())
    monkeypatch.setattr("webapp._chat_ips", {})
    r = cliente.post("/api/chat", json={"mensajes": [{"rol": "user", "texto": "¿precios?"}]})
    assert r.status_code == 200
    assert r.get_json()["respuesta"] == "Respuesta de prueba."


def test_endpoint_chat_valida_cuerpo(cliente, monkeypatch):
    monkeypatch.setattr("webapp.IA_CFG", _cfg_activa())
    monkeypatch.setattr("webapp._chat_ips", {})
    assert cliente.post("/api/chat", json={"mensajes": []}).status_code == 400


def test_activar_asesor_avisa_cada_vez(cliente, monkeypatch, _sin_smtp_real):
    """Cada activación de 'quiere asesor' notifica al negocio, aunque ya
    estuviera activada antes (un lead que insiste no debe perderse)."""
    import src.correo as correo
    monkeypatch.setattr(correo, "cargar_config_email", lambda: {
        "habilitado": True, "user": "smtp@test.co", "notificar_a": "negocio@test.co"})
    _login_cliente(cliente)

    r1 = cliente.post("/api/mi-cuenta/preferencias", json={"quiere_asesor": True})
    r2 = cliente.post("/api/mi-cuenta/preferencias", json={"quiere_asesor": True})
    assert r1.get_json()["aviso_enviado"] is True
    assert r2.get_json()["aviso_enviado"] is True
    assert len(_sin_smtp_real) == 2

    # desactivar nunca notifica
    cliente.post("/api/mi-cuenta/preferencias", json={"quiere_asesor": False})
    assert len(_sin_smtp_real) == 2


def test_widget_oculto_si_desactivado(cliente, monkeypatch):
    # Ver la página NUNCA exige login — el widget se oculta solo si la IA está apagada.
    monkeypatch.setattr("webapp.IA_CFG", {"habilitado": False, "api_key": ""})
    assert 'id="chat-fab"' not in cliente.get("/").data.decode()


def test_widget_visible_si_activo(cliente, monkeypatch):
    # Visible sin necesidad de iniciar sesión (la barrera está en interactuar, no en ver).
    monkeypatch.setattr("webapp.IA_CFG", _cfg_activa())
    assert 'id="chat-fab"' in cliente.get("/").data.decode()


# ------------------- contexto del usuario en el prompt ----------------------

def test_contexto_vacio_sin_usuario_ni_liquidacion():
    assert asistente._contexto_usuario(None, None) == ""


def test_contexto_incluye_nombre_y_vencimiento():
    from datetime import date

    usuario = types.SimpleNamespace(nombre="Ana Ruiz", cedula="1234",
                                    fecha_limite=date(2026, 8, 12))
    ctx = asistente._contexto_usuario(usuario, None)
    assert "Ana Ruiz" in ctx
    assert "12/08/2026" in ctx


def test_contexto_avisa_cuando_falta_la_cedula():
    usuario = types.SimpleNamespace(nombre="Ana", cedula=None, fecha_limite=None)
    ctx = asistente._contexto_usuario(usuario, None)
    assert "NO ha registrado su cédula" in ctx


def test_contexto_reporta_saldo_a_pagar_y_a_favor(parametros):
    from src.modelos import DatosDeclaracion, SubcedulaGeneral
    from src.motor_calculo import calcular

    pagar = calcular(DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=300_000_000),
        aplicar_renta_exenta_25=False, patrimonio_bruto=1), parametros)
    assert "SALDO A PAGAR" in asistente._contexto_usuario(None, pagar)

    favor = calcular(DatosDeclaracion(
        trabajo=SubcedulaGeneral(ingresos_brutos=80_000_000),
        retenciones=20_000_000, patrimonio_bruto=1), parametros)
    assert "SALDO A FAVOR" in asistente._contexto_usuario(None, favor)


def test_contexto_no_filtra_nit_ni_patrimonio(parametros):
    """Privacidad: el prompt no debe llevar identificadores ni el patrimonio."""
    from src.modelos import (DatosContribuyente, DatosDeclaracion,
                             SubcedulaGeneral)
    from src.motor_calculo import calcular

    datos = DatosDeclaracion(
        contribuyente=DatosContribuyente(nit="900123456"),
        trabajo=SubcedulaGeneral(ingresos_brutos=100_000_000),
        patrimonio_bruto=987_654_321)
    usuario = types.SimpleNamespace(nombre="Ana", cedula="900123456", fecha_limite=None)
    ctx = asistente._contexto_usuario(usuario, calcular(datos, parametros))
    assert "900123456" not in ctx
    assert "987" not in ctx
