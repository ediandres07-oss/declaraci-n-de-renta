"""Flujo comercial de la landing: verificación, checkout y desbloqueo por pago."""
import json

import pytest

from webapp import app
from .conftest import EXOGENA


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    import webapp as w
    from src.auth import OrdenRegistro, db
    with app.app_context():           # órdenes ahora viven en la BD: tabla limpia por test
        OrdenRegistro.query.delete()
        db.session.commit()
    monkeypatch.setattr(w, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(w, "CLIENTES_DIR", tmp_path / "clientes")
    app.config["TESTING"] = True
    with app.test_client() as c:
        # inicia sesión como personal autorizado (para /admin, /liquidador,
        # confirmar-pago). Los endpoints públicos de la landing no lo requieren.
        _login_autorizado(c)
        yield c


def _login_autorizado(cliente):
    """Crea/usa un usuario autorizado (config/acceso.yaml) y abre su sesión."""
    from src.auth import db, Usuario, _correos_autorizados
    email = next(iter(_correos_autorizados()), "staff@test.com")
    with app.app_context():
        u = Usuario.query.filter_by(email=email).first()
        if u is None:
            u = Usuario(proveedor="google", proveedor_id="staff", email=email, nombre="Staff")
            db.session.add(u)
            db.session.commit()
        elif u.proveedor not in ("google", "microsoft"):
            u.proveedor = "google"
            db.session.commit()
        uid = u.id
    with cliente.session_transaction() as s:
        s["uid"] = uid


def _cargar(cliente):
    with open(EXOGENA, "rb") as fh:
        return cliente.post("/api/cargar-landing",
                            data={"exogena": (fh, "exogena.xlsx")},
                            content_type="multipart/form-data")


def test_landing_carga_muestra_solo_resultado_comercial(cliente):
    r = _cargar(cliente)
    assert r.status_code == 200
    j = r.get_json()
    assert j["obligado"] is True and len(j["razones"]) == 3
    assert j["nit_final"] == "30"
    assert j["fecha_limite_iso"] == "2026-09-02"    # NIT termina en 30 → 2/sep/2026
    assert j["valor_a_pagar"] > 0
    # el liquidador queda oculto: no se exponen renglones ni partidas
    assert "renglones" not in j and "partidas" not in j and "datos" not in j


def test_pdf_bloqueado_hasta_pagar(cliente):
    token = _cargar(cliente).get_json()["token"]
    r = cliente.post("/api/checkout", json={
        "token": token, "plan": "pdf",
        "contacto": {"nombre": "Eli", "email": "eli@test.com"}})
    assert r.status_code == 200
    orden = r.get_json()["orden_id"]

    r = cliente.get(f"/api/orden/{orden}/formulario.pdf")
    assert r.status_code == 402                      # sin pago no hay PDF

    r = cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    assert r.get_json()["estado"] == "pagada"

    r = cliente.get(f"/api/orden/{orden}/formulario.pdf")
    assert r.status_code == 200
    assert r.data[:5] == b"%PDF-"


def test_checkout_requiere_contacto(cliente):
    token = _cargar(cliente).get_json()["token"]
    r = cliente.post("/api/checkout", json={"token": token, "plan": "pdf", "contacto": {}})
    assert r.status_code == 400


def test_plan_presentacion_queda_en_tramite(cliente):
    token = _cargar(cliente).get_json()["token"]
    orden = cliente.post("/api/checkout", json={
        "token": token, "plan": "presentacion",
        "contacto": {"telefono": "3000000000"}}).get_json()["orden_id"]
    r = cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    assert r.get_json()["estado"] == "pagada_en_tramite"


def test_landing_html_sirve(cliente):
    r = cliente.get("/")
    html = r.data.decode()
    assert "declaración de renta" in html.lower()
    assert "exógena" in html.lower()
    r2 = cliente.get("/liquidador")
    assert r2.status_code == 200 and "Dependientes" in r2.data.decode()


def test_recalcular_con_dependientes_rebaja_el_pago(cliente):
    j = _cargar(cliente).get_json()
    base = j["valor_a_pagar"]
    r = cliente.post("/api/recalcular-landing", json={"token": j["token"], "dependientes": 2})
    assert r.status_code == 200
    k = r.get_json()
    assert k["valor_a_pagar"] < base            # 2 dependientes rebajan el pago
    assert k["ahorro"] == pytest.approx(base - k["valor_a_pagar"], abs=1)

    # la elección queda guardada: el PDF pagado sale con la deducción
    orden = cliente.post("/api/checkout", json={
        "token": j["token"], "plan": "pdf",
        "contacto": {"email": "x@y.co"}}).get_json()["orden_id"]
    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    pdf = cliente.get(f"/api/orden/{orden}/formulario.pdf")
    assert pdf.status_code == 200
    import io
    from pypdf import PdfReader
    campos = PdfReader(io.BytesIO(pdf.data)).get_fields()
    assert campos["R139"]["/V"] == "7,171,000"  # R139: 2 × 72 UVT


def test_recalcular_sin_token_falla(cliente):
    r = cliente.post("/api/recalcular-landing", json={"token": "nope", "dependientes": 1})
    assert r.status_code == 400


def test_cargar_devuelve_patrimonio_detectado(cliente):
    j = _cargar(cliente).get_json()
    assert j["patrimonio_bruto"] > 0            # la exógena de prueba trae patrimonio
    assert "deudas" in j


def test_recalcular_patrimonio_no_borra_dependientes(cliente):
    """Corregir el patrimonio conserva los dependientes ya elegidos, y al
    revés: cada campo del recálculo es independiente y todo queda guardado."""
    j = _cargar(cliente).get_json()
    cliente.post("/api/recalcular-landing", json={"token": j["token"], "dependientes": 2})

    r = cliente.post("/api/recalcular-landing", json={
        "token": j["token"], "patrimonio_bruto": 350_000_000, "deudas": 80_000_000})
    assert r.status_code == 200
    k = r.get_json()
    assert k["patrimonio_bruto"] == 350_000_000
    assert k["deudas"] == 80_000_000
    assert k["dependientes"] == 2               # no se borraron

    # y viceversa: cambiar dependientes no pisa el patrimonio corregido
    r2 = cliente.post("/api/recalcular-landing", json={"token": j["token"], "dependientes": 1})
    assert r2.get_json()["patrimonio_bruto"] == 350_000_000

    # quedó persistido en la orden para el PDF pagado
    import webapp as w
    datos = w._leer_ordenes()[j["token"]]["datos"]
    assert datos["patrimonio_bruto"] == 350_000_000
    assert datos["deudas"] == 80_000_000


def test_recalcular_patrimonio_invalido_falla(cliente):
    j = _cargar(cliente).get_json()
    for malo in (-1, "no-numero", 1e14):
        r = cliente.post("/api/recalcular-landing",
                         json={"token": j["token"], "patrimonio_bruto": malo})
        assert r.status_code == 400, f"debió rechazar {malo}"


def test_plan_recomendado_guarda_el_archivo(cliente, tmp_path):
    import webapp as w
    j = _cargar(cliente).get_json()
    # el archivo queda en espera en uploads
    assert (w.UPLOADS_DIR / f"{j['token']}.xlsx").exists()

    orden = cliente.post("/api/checkout", json={
        "token": j["token"], "plan": "presentacion",
        "contacto": {"email": "x@y.co"}}).get_json()["orden_id"]
    cliente.post("/api/confirmar-pago", json={"orden_id": orden})

    # aceptado el recomendado: copia permanente para el trámite
    guardados = list(w.CLIENTES_DIR.glob(f"{orden}_Exogena_*.xlsx"))
    assert len(guardados) == 1
    assert "44004730" in guardados[0].name

    # y el Excel queda en la BD (sobrevive redeploys): por token y por orden
    from src.auth import ArchivoExogena
    with app.app_context():
        assert ArchivoExogena.query.get(j["token"]) is not None
        copia = ArchivoExogena.query.get(f"orden:{orden}")
        assert copia is not None and copia.datos[:2] == b"PK"   # es un xlsx real

    # el personal puede descargarla desde /admin
    r = cliente.get(f"/api/orden/{orden}/exogena.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_eliminar_borra_el_excel_de_la_bd(cliente):
    """Si el cliente no continúa, su Excel también sale de la BD."""
    from src.auth import ArchivoExogena
    j = _cargar(cliente).get_json()
    with app.app_context():
        assert ArchivoExogena.query.get(j["token"]) is not None
    cliente.post("/api/eliminar-datos", json={"token": j["token"]})
    with app.app_context():
        assert ArchivoExogena.query.get(j["token"]) is None


def test_si_no_acepta_puede_eliminar(cliente):
    import webapp as w
    j = _cargar(cliente).get_json()
    archivo = w.UPLOADS_DIR / f"{j['token']}.xlsx"
    assert archivo.exists()

    # crea una orden que nunca paga
    cliente.post("/api/checkout", json={"token": j["token"], "plan": "pdf",
                                        "contacto": {"email": "x@y.co"}})
    r = cliente.post("/api/eliminar-datos", json={"token": j["token"]})
    assert r.status_code == 200 and r.get_json()["eliminado"] is True
    assert not archivo.exists()                      # archivo borrado
    # los datos también: recalcular ya no funciona
    r = cliente.post("/api/recalcular-landing", json={"token": j["token"], "dependientes": 1})
    assert r.status_code == 400
    # y no quedan órdenes pendientes suyas
    ordenes = w._leer_ordenes()
    assert not any(o.get("token") == j["token"] for o in ordenes.values())


def test_eliminar_no_borra_tramite_pagado(cliente):
    import webapp as w
    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={
        "token": j["token"], "plan": "presentacion",
        "contacto": {"email": "x@y.co"}}).get_json()["orden_id"]
    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    cliente.post("/api/eliminar-datos", json={"token": j["token"]})
    # la copia del trámite aceptado se conserva y la orden pagada también
    assert list(w.CLIENTES_DIR.glob(f"{orden}_Exogena_*.xlsx"))
    assert w._leer_ordenes()[orden]["estado"] == "pagada_en_tramite"


def test_checklist_documentos_con_tramite_pagado(cliente):
    import webapp as w
    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={
        "token": j["token"], "plan": "presentacion",
        "contacto": {"email": "x@y.co"}}).get_json()["orden_id"]

    # sin pago, el checklist también está bloqueado
    assert cliente.get(f"/api/orden/{orden}/documentos.pdf").status_code == 402

    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    r = cliente.get(f"/api/orden/{orden}/documentos.pdf")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"

    import io
    from pypdf import PdfReader
    texto = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.data)).pages)
    assert "Documentos para su declaración de renta" in texto
    assert "RUT actualizado" in texto
    assert "ELIZABETH" in texto            # personalizado
    assert "2026-09-02" in texto           # su fecha límite

    # copia del checklist guardada junto al trámite para control interno
    assert list(w.CLIENTES_DIR.glob(f"{orden}_Documentos_*.pdf"))


def test_reportar_pago_no_desbloquea_hasta_confirmar(cliente):
    """El cliente reporta la consignación pero el PDF solo se libera cuando
    el administrador verifica que llegó a la cuenta Bancolombia."""
    j = _cargar(cliente).get_json()
    r = cliente.post("/api/checkout", json={"token": j["token"], "plan": "pdf",
                                            "contacto": {"email": "x@y.co"}})
    assert "numero" in r.get_json()["pago"]      # instrucciones de consignación
    orden = r.get_json()["orden_id"]

    r = cliente.post("/api/reportar-pago", json={"orden_id": orden})
    assert r.get_json()["estado"] == "pago_reportado"
    assert cliente.get(f"/api/orden/{orden}/formulario.pdf").status_code == 402

    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    assert cliente.get(f"/api/orden/{orden}/formulario.pdf").status_code == 200


def test_reportar_pago_avisa_al_negocio(cliente, monkeypatch, _sin_smtp_real):
    """Cuando el cliente reporta la consignación, el negocio recibe un aviso
    por correo con los datos de la orden (antes solo quedaba en /admin)."""
    import src.correo as correo
    monkeypatch.setattr(correo, "cargar_config_email", lambda: {
        "habilitado": True, "user": "smtp@test.co", "notificar_a": "negocio@test.co"})

    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={"token": j["token"], "plan": "pdf",
        "contacto": {"nombre": "Ana", "email": "ana@test.co", "telefono": "300"}}
        ).get_json()["orden_id"]
    cliente.post("/api/reportar-pago", json={"orden_id": orden})

    assert len(_sin_smtp_real) == 1
    aviso = _sin_smtp_real[0]
    assert aviso["destino"] == "negocio@test.co"
    assert "por verificar" in aviso["asunto"]
    assert orden.upper() in aviso["asunto"]
    from webapp import PLANES
    precio_pdf = f"{PLANES['pdf']['precio']:,.0f}".replace(",", ".")   # precio vigente del plan PDF
    assert "ana@test.co" in aviso["html"] and precio_pdf in aviso["html"]

    # reportar dos veces no duplica el aviso (el estado ya no es pendiente_pago)
    cliente.post("/api/reportar-pago", json={"orden_id": orden})
    assert len(_sin_smtp_real) == 1


def test_pago_confirmado_avisa_una_sola_vez(cliente, monkeypatch, _sin_smtp_real):
    """La confirmación (pasarela o admin) avisa con '✓ confirmado'; si el
    webhook se repite, el aviso no se duplica."""
    import src.correo as correo
    monkeypatch.setattr(correo, "cargar_config_email", lambda: {
        "habilitado": True, "user": "smtp@test.co"})

    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={"token": j["token"], "plan": "presentacion",
        "contacto": {"email": "x@y.co"}}).get_json()["orden_id"]

    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    confirmados = [a for a in _sin_smtp_real if "confirmado" in a["asunto"]]
    assert len(confirmados) == 1
    assert "189.900" in confirmados[0]["html"]

    cliente.post("/api/confirmar-pago", json={"orden_id": orden})   # webhook repetido
    confirmados = [a for a in _sin_smtp_real if "confirmado" in a["asunto"]]
    assert len(confirmados) == 1


def test_pago_pdf_confirmado_entrega_al_cliente(cliente, monkeypatch, _sin_smtp_real):
    """Al confirmar el pago del plan PDF, el cliente recibe por correo su
    Formulario 210 + la guía (dos adjuntos) y links de descarga."""
    import src.correo as correo
    monkeypatch.setattr(correo, "cargar_config_email", lambda: {
        "habilitado": True, "user": "smtp@test.co"})

    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={"token": j["token"], "plan": "pdf",
        "contacto": {"nombre": "Ana Ruiz", "email": "ana@test.co"}}).get_json()["orden_id"]

    cliente.post("/api/confirmar-pago", json={"orden_id": orden})

    entregas = [a for a in _sin_smtp_real if a["destino"] == "ana@test.co"]
    assert len(entregas) == 1
    e = entregas[0]
    assert len(e["adjuntos"]) == 2                       # formulario + guía
    nombres = [n for (n, _b, _m) in e["adjuntos"]]
    assert any("Formulario210" in n for n in nombres)
    assert any("Guia" in n for n in nombres)
    for (_n, datos, mimetype) in e["adjuntos"]:
        assert datos[:5] == b"%PDF-" and mimetype == "application/pdf"
    assert f"/api/orden/{orden}/formulario.pdf" in e["html"]    # link de descarga
    assert f"/api/orden/{orden}/guia-dian.pdf" in e["html"]

    # confirmar de nuevo (webhook repetido) no reenvía la entrega
    cliente.post("/api/confirmar-pago", json={"orden_id": orden})
    entregas = [a for a in _sin_smtp_real if a["destino"] == "ana@test.co"]
    assert len(entregas) == 1


def test_muestra_contador_una_gratis(cliente):
    """Un contador (usuario logueado) obtiene 1 Formulario 210 de MUESTRA gratis;
    una segunda declaración distinta queda bloqueada hasta pagar el pase."""
    from src.auth import MuestraContador, db
    with app.app_context():
        MuestraContador.query.delete()
        db.session.commit()

    j = _cargar(cliente).get_json()
    r = cliente.get(f"/api/muestra-contador/{j['token']}.pdf")
    assert r.status_code == 200
    assert r.data[:5] == b"%PDF-"                       # es un PDF

    # re-descargar la MISMA muestra: permitido (no cuenta como segunda)
    r = cliente.get(f"/api/muestra-contador/{j['token']}.pdf")
    assert r.status_code == 200

    # una segunda exógena distinta: bloqueada (ya usó su prueba)
    j2 = _cargar(cliente).get_json()
    r = cliente.get(f"/api/muestra-contador/{j2['token']}.pdf")
    assert r.status_code == 402
    assert "muestra" in r.get_json()["error"].lower()


def test_muestra_contador_requiere_carga(cliente):
    """Sin exógena cargada, la muestra responde error (no revienta)."""
    r = cliente.get("/api/muestra-contador/token-inexistente.pdf")
    assert r.status_code == 400


def test_reset_muestra_devuelve_la_prueba(cliente):
    """Reiniciar desde /admin le devuelve la prueba gratis al contador."""
    from src.auth import MuestraContador, db
    with app.app_context():
        MuestraContador.query.delete()
        db.session.commit()

    j = _cargar(cliente).get_json()
    assert cliente.get(f"/api/muestra-contador/{j['token']}.pdf").status_code == 200
    with app.app_context():
        uid = MuestraContador.query.first().usuario_id

    j2 = _cargar(cliente).get_json()                       # segunda distinta: bloqueada
    assert cliente.get(f"/api/muestra-contador/{j2['token']}.pdf").status_code == 402

    r = cliente.post("/api/muestra-contador/reset", json={"usuario_id": uid})
    assert r.status_code == 200
    # tras reiniciar, vuelve a poder generar una muestra
    assert cliente.get(f"/api/muestra-contador/{j2['token']}.pdf").status_code == 200


def test_pase_contador_pago_habilita_acceso(cliente):
    """Pase de contadores: crear orden → reportar → confirmar habilita el acceso
    al liquidador con el correo del comprador (automático)."""
    import webapp as w
    from src.auth import AccesoAutorizado, db
    with app.app_context():
        AccesoAutorizado.query.delete()
        db.session.commit()

    oid = cliente.post("/api/pase-contador/crear", json={}).get_json()["orden_id"]
    assert cliente.post("/api/reportar-pago", json={"orden_id": oid}).status_code == 200
    assert cliente.post("/api/confirmar-pago", json={"orden_id": oid}).status_code == 200

    orden = w._leer_ordenes()[oid]
    assert orden["estado"] == "pagada" and orden["plan"] == "contadores"
    email = orden["contacto"]["email"].lower()
    with app.app_context():
        assert db.session.get(AccesoAutorizado, email) is not None


def test_admin_lista_ordenes(cliente):
    j = _cargar(cliente).get_json()
    orden = cliente.post("/api/checkout", json={"token": j["token"], "plan": "presentacion",
                                                "contacto": {"telefono": "300"}}).get_json()["orden_id"]
    cliente.post("/api/reportar-pago", json={"orden_id": orden})
    html = cliente.get("/admin").data.decode()
    assert orden in html
    assert "pago reportado" in html
    assert "00514294607" in html


def test_liquidador_y_admin_bloqueados_sin_autorizacion(tmp_path, monkeypatch):
    """El acceso profesional (/liquidador, /admin, confirmar-pago) exige personal autorizado."""
    import webapp as w
    from src.auth import db, Usuario
    app.config["TESTING"] = True
    with app.test_client() as c:
        # sin sesión → redirige a login
        assert c.get("/liquidador").status_code == 302
        assert c.get("/admin").status_code == 302
        assert c.post("/api/confirmar-pago", json={"orden_id": "x"}).status_code == 302
        # con sesión de un usuario NO autorizado → 403
        with app.app_context():
            Usuario.query.filter_by(email="intruso@test.com").delete(); db.session.commit()
            u = Usuario(proveedor="demo", proveedor_id="i", email="intruso@test.com", nombre="Intruso")
            db.session.add(u); db.session.commit(); uid = u.id
        with c.session_transaction() as s:
            s["uid"] = uid
        assert c.get("/liquidador").status_code == 403
        assert c.get("/admin").status_code == 403
        assert c.post("/api/confirmar-pago", json={"orden_id": "x"}).status_code == 403
        with app.app_context():
            Usuario.query.filter_by(email="intruso@test.com").delete(); db.session.commit()


def test_demo_con_correo_admin_no_es_autorizado():
    """Un ingreso 'demo' con un correo autorizado NO debe dar acceso de admin."""
    from src.auth import es_autorizado, _correos_autorizados

    class _U:
        def __init__(self, proveedor, email):
            self.proveedor = proveedor; self.email = email

    correo_admin = next(iter(_correos_autorizados()), "staff@test.com")
    assert es_autorizado(_U("demo", correo_admin)) is False       # demo NO
    assert es_autorizado(_U("google", correo_admin)) is True      # google SÍ
    assert es_autorizado(_U("google", "otro@test.com")) is False  # no está en la lista
