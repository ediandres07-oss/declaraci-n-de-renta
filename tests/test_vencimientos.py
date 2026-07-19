"""Gestor de vencimientos para contadores: motor de fechas, CRUD, ICS y permisos."""
from datetime import date

import pytest

import io

import webapp
from src.auth import Usuario, db
from src.vencimientos import (ClienteContador, PerfilContador, VencimientoAviso,
                              VencimientoEstado, _clave_aplica, analizar_rut,
                              cargar_calendario, catalogo_obligaciones,
                              recordatorios_vencimientos, vencimientos_de)


@pytest.fixture()
def cliente():
    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        yield c


@pytest.fixture()
def contador(cliente):
    """Usuario logueado (contador) + limpieza de sus clientes al salir."""
    with webapp.app.app_context():
        u = Usuario.query.filter_by(email="contador.venc@ejemplo.com").first()
        if u is None:
            u = Usuario(proveedor="google", proveedor_id="venc-test",
                        email="contador.venc@ejemplo.com", nombre="Conta Dora")
            db.session.add(u)
            db.session.commit()
        uid = u.id
    with cliente.session_transaction() as s:
        s["uid"] = uid
    yield uid
    with webapp.app.app_context():
        ids = [c2.id for c2 in ClienteContador.query.filter_by(usuario_id=uid)]
        if ids:
            VencimientoEstado.query.filter(
                VencimientoEstado.cliente_id.in_(ids)).delete()
            ClienteContador.query.filter_by(usuario_id=uid).delete()
        VencimientoAviso.query.filter_by(usuario_id=uid).delete()
        PerfilContador.query.filter_by(usuario_id=uid).delete()
        db.session.commit()


# ---------------------------------------------------------------- motor
def test_calendario_2026_carga_y_tiene_obligaciones():
    cal = cargar_calendario(2026)
    assert cal["ano"] == 2026
    claves = {o["clave"] for o in catalogo_obligaciones()}
    assert {"renta_pn", "renta_pj", "iva_bimestral", "retefuente",
            "exogena", "rst"} <= claves


def test_clave_aplica_rangos_y_cero_al_final():
    assert _clave_aplica("3", 3, 1)
    assert not _clave_aplica("3", 4, 1)
    assert _clave_aplica("9-0", 0, 1)          # el 0 cierra el ciclo
    assert _clave_aplica("9-0", 9, 1)
    assert not _clave_aplica("9-0", 1, 1)
    assert _clave_aplica("96-00", 0, 2)        # NIT terminado en 00
    assert _clave_aplica("01-05", 3, 2)
    assert _clave_aplica("todos", 7, 1)


def test_retefuente_enero_digito_1_es_10_febrero():
    eventos = vencimientos_de("900123451", ["retefuente"])
    assert eventos[0]["fecha"] == date(2026, 2, 10)
    assert len(eventos) == 12                   # 12 periodos mensuales


def test_renta_pn_dos_digitos_coincide_con_calendario_oficial():
    # cédula terminada en 01 → 12-ago-2026 (primer turno del calendario DIAN)
    eventos = vencimientos_de("1030601", ["renta_pn"])
    assert eventos == [dict(eventos[0])]        # una sola fecha
    assert eventos[0]["fecha"] == date(2026, 8, 12)
    # terminada en 00 → último turno: 26-oct-2026
    assert vencimientos_de("52000", ["renta_pn"])[0]["fecha"] == date(2026, 10, 26)


def test_patrimonio_incluye_cuota_fija_para_todos():
    eventos = vencimientos_de("800200305", ["patrimonio"])
    fechas = {e["fecha"] for e in eventos}
    assert date(2026, 9, 14) in fechas          # 2ª cuota: todos los NIT


def test_nit_sin_digitos_suficientes_no_revienta():
    assert vencimientos_de("7", ["renta_pn"]) == []
    assert vencimientos_de("", ["retefuente"]) == []


# ---------------------------------------------------------------- API CRUD
def test_api_exige_login(cliente):
    assert cliente.get("/api/vencimientos/clientes").status_code == 401
    assert cliente.get("/vencimientos").status_code == 302


def test_crear_listar_editar_borrar_cliente(cliente, contador):
    r = cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Panadería La Espiga SAS", "nit": "901.234.567-8",
        "tipo": "juridica", "obligaciones": ["renta_pj", "retefuente"]})
    assert r.status_code == 200
    creado = r.get_json()["cliente"]
    assert creado["nit"] == "9012345678"        # se limpia a solo dígitos

    r = cliente.get("/api/vencimientos/clientes")
    lista = r.get_json()["clientes"]
    assert len(lista) == 1 and lista[0]["proximo"] is not None

    r = cliente.put(f"/api/vencimientos/clientes/{creado['id']}",
                    json={"nombre": "La Espiga SAS", "obligaciones": ["renta_pj"]})
    assert r.get_json()["cliente"]["obligaciones"] == ["renta_pj"]

    r = cliente.delete(f"/api/vencimientos/clientes/{creado['id']}")
    assert r.status_code == 200
    assert cliente.get("/api/vencimientos/clientes").get_json()["clientes"] == []


def test_cliente_sin_nombre_o_nit_rechazado(cliente, contador):
    assert cliente.post("/api/vencimientos/clientes",
                        json={"nombre": "", "nit": "123"}).status_code == 400
    assert cliente.post("/api/vencimientos/clientes",
                        json={"nombre": "X", "nit": "1"}).status_code == 400


def test_obligaciones_invalidas_se_filtran(cliente, contador):
    r = cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601",
        "obligaciones": ["renta_pn", "no_existe"]})
    assert r.get_json()["cliente"]["obligaciones"] == ["renta_pn"]


def test_agenda_y_estado(cliente, contador):
    cid = cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601", "tipo": "natural",
        "obligaciones": ["renta_pn"]}).get_json()["cliente"]["id"]
    ag = cliente.get("/api/vencimientos/agenda").get_json()
    assert len(ag["eventos"]) == 1
    ev = ag["eventos"][0]
    assert ev["estado"] == "pendiente" and ev["fecha"] == "2026-08-12"

    r = cliente.post("/api/vencimientos/estado", json={
        "cliente_id": cid, "clave": ev["clave"], "estado": "presentada"})
    assert r.status_code == 200
    ag = cliente.get("/api/vencimientos/agenda").get_json()
    assert ag["eventos"][0]["estado"] == "presentada"

    # volver a pendiente elimina el registro
    cliente.post("/api/vencimientos/estado", json={
        "cliente_id": cid, "clave": ev["clave"], "estado": "pendiente"})
    with webapp.app.app_context():
        assert VencimientoEstado.query.filter_by(cliente_id=cid).count() == 0


def test_no_puede_tocar_clientes_ajenos(cliente, contador):
    with webapp.app.app_context():
        otro = Usuario.query.filter_by(email="otro.venc@ejemplo.com").first()
        if otro is None:
            otro = Usuario(proveedor="google", proveedor_id="venc-otro",
                           email="otro.venc@ejemplo.com", nombre="Otro")
            db.session.add(otro)
            db.session.commit()
        ajeno = ClienteContador(usuario_id=otro.id, nombre="Ajeno", nit="99",
                                obligaciones="[]")
        db.session.add(ajeno)
        db.session.commit()
        ajeno_id = ajeno.id
    try:
        assert cliente.put(f"/api/vencimientos/clientes/{ajeno_id}",
                           json={"nombre": "Hackeado"}).status_code == 404
        assert cliente.delete(
            f"/api/vencimientos/clientes/{ajeno_id}").status_code == 404
    finally:
        with webapp.app.app_context():
            ClienteContador.query.filter_by(id=ajeno_id).delete()
            db.session.commit()


# ---------------------------------------------------------------- ICS y PDF
def test_ics_descarga_y_suscripcion(cliente, contador):
    cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana, la de siempre", "nit": "1030601",
        "obligaciones": ["renta_pn"]})
    r = cliente.get("/api/vencimientos/ical")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    texto = r.data.decode()
    assert "BEGIN:VEVENT" in texto and "DTSTART;VALUE=DATE:20260812" in texto
    assert "Ana\\, la de siempre" in texto      # comas escapadas (RFC 5545)

    url = cliente.get("/api/vencimientos/ical?url=1").get_json()["url"]
    ruta = url.split("http://localhost", 1)[-1]
    r = cliente.get(ruta)                        # sin sesión igual funciona…
    assert r.status_code == 200 and b"BEGIN:VCALENDAR" in r.data
    # …pero con firma inválida no
    mala = ruta.rsplit("-", 1)[0] + "-abcdef0123456789abcdef01.ics"
    assert cliente.get(mala).status_code == 403


def test_informe_pdf(cliente, contador):
    cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601", "obligaciones": ["renta_pn"]})
    r = cliente.get("/api/vencimientos/informe.pdf?dias=365")
    assert r.status_code == 200
    assert r.data[:5] == b"%PDF-"


def test_pagina_carga_con_login(cliente, contador):
    r = cliente.get("/vencimientos")
    assert r.status_code == 200
    assert "Vencimientos tributarios" in r.data.decode()


# ---------------------------------------------------------------- importar Excel
def _xlsx_en_memoria(filas):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["nombre", "nit", "tipo", "correo", "telefono", "obligaciones"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_plantilla_importar_descarga(cliente, contador):
    r = cliente.get("/api/vencimientos/plantilla.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"


def test_importar_excel_crea_y_reporta_errores(cliente, contador):
    buf = _xlsx_en_memoria([
        ["La Espiga SAS", "901234567", "juridica", "e@x.co", "300111",
         "renta_pj, iva_bimestral"],
        ["Ana Pérez", "1030601234", "natural", "", "", ""],
        ["Sin Nit", "", "natural", "", "", ""],                 # error
        ["Duplicada", "901234567", "juridica", "", "", ""],     # NIT repetido
    ])
    r = cliente.post("/api/vencimientos/importar",
                     data={"archivo": (buf, "clientes.xlsx")},
                     content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["creados"] == 2
    assert len(j["errores"]) == 2
    lista = cliente.get("/api/vencimientos/clientes").get_json()["clientes"]
    por_nit = {c["nit"]: c for c in lista}
    assert por_nit["901234567"]["obligaciones"] == ["renta_pj", "iva_bimestral"]
    # sin obligaciones en el archivo → sugeridas por tipo
    assert por_nit["1030601234"]["obligaciones"] == ["renta_pn"]


def test_importar_csv(cliente, contador):
    csv = ("nombre,nit,tipo,correo,telefono,obligaciones\n"
           "Tienda Rosa,52987654,natural,,,\n").encode()
    r = cliente.post("/api/vencimientos/importar",
                     data={"archivo": (io.BytesIO(csv), "clientes.csv")},
                     content_type="multipart/form-data")
    assert r.get_json()["creados"] == 1


# ---------------------------------------------------------------- leer RUT
TEXTO_RUT_PJ = """
    5. Número de Identificación Tributaria (NIT) 9 0 1 2 3 4 5 6 7 6. DV 8
    Persona jurídica
    35. Razón social: COMERCIALIZADORA EL ROBLE SAS 36. Nombre comercial
    53. Responsabilidades, Calidades y Atributos
    05- Impto. renta y compl. régimen ordinario 07- Retención en la fuente a título de renta
    14- Informante de exogena 48- Impuesto sobre las ventas
"""


def test_analizar_rut_persona_juridica():
    d = analizar_rut(TEXTO_RUT_PJ)
    assert d["nit"] == "901234567"
    assert d["tipo"] == "juridica"
    assert "COMERCIALIZADORA EL ROBLE SAS" in d["nombre"]
    assert set(d["codigos"]) == {"05", "07", "14", "48"}
    assert set(d["obligaciones"]) == {"renta_pj", "retefuente", "exogena",
                                      "iva_bimestral"}


def test_analizar_rut_regimen_simple():
    d = analizar_rut("Persona jurídica 47- Régimen simple de tributación")
    assert d["tipo"] == "rst"
    assert "rst" in d["obligaciones"] and "rst_anticipos" in d["obligaciones"]


def test_api_rut_pdf(cliente, contador):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    y = 750
    for linea in TEXTO_RUT_PJ.strip().splitlines():
        c.drawString(40, y, linea.strip())
        y -= 18
    c.save()
    buf.seek(0)
    r = cliente.post("/api/vencimientos/rut",
                     data={"archivo": (buf, "rut.pdf")},
                     content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["nit"] == "901234567"
    assert "renta_pj" in j["obligaciones"]


def test_api_rut_escaneado_falla_claro(cliente, contador):
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    canvas.Canvas(buf).save()          # PDF sin texto
    buf.seek(0)
    r = cliente.post("/api/vencimientos/rut",
                     data={"archivo": (buf, "rut.pdf")},
                     content_type="multipart/form-data")
    assert r.status_code == 422


# ---------------------------------------------------------------- perfil / logo
def _png_valido() -> bytes:
    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.new("RGB", (40, 20), (30, 36, 50)).save(buf, "PNG")
    return buf.getvalue()


def test_perfil_estudio_y_logo(cliente, contador):
    r = cliente.post("/api/vencimientos/perfil",
                     data={"estudio": "Pérez & Asociados",
                           "logo": (io.BytesIO(_png_valido()), "logo.png",
                                    "image/png")},
                     content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json()["tiene_logo"] is True
    j = cliente.get("/api/vencimientos/perfil").get_json()
    assert j["estudio"] == "Pérez & Asociados" and j["tiene_logo"] is True
    assert cliente.get("/api/vencimientos/perfil/logo").status_code == 200
    # el informe PDF sigue generándose con logo configurado
    cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601", "obligaciones": ["renta_pn"]})
    r = cliente.get("/api/vencimientos/informe.pdf?dias=365")
    assert r.status_code == 200 and r.data[:5] == b"%PDF-"


def test_perfil_rechaza_logo_gigante(cliente, contador):
    r = cliente.post("/api/vencimientos/perfil",
                     data={"logo": (io.BytesIO(b"x" * 600 * 1024), "logo.png",
                                    "image/png")},
                     content_type="multipart/form-data")
    assert r.status_code == 400


# ---------------------------------------------------------------- recordatorios
def test_recordatorios_7_dias_y_dedup(cliente, contador, _sin_smtp_real):
    from datetime import date as d
    from src.vencimientos import enviar_avisos_vencimientos
    cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601", "obligaciones": ["renta_pn"]})
    hoy = d(2026, 8, 5)                     # renta_pn ...01 vence 12-ago → 7 días
    with webapp.app.app_context():
        pendientes = recordatorios_vencimientos(hoy)
        mios = [p for p in pendientes
                if p["usuario"].email == "contador.venc@ejemplo.com"]
        assert len(mios) == 1 and mios[0]["eventos"][0]["dias"] == 7

        enviar_avisos_vencimientos(hoy)
        correos = [e for e in _sin_smtp_real
                   if e["destino"] == "contador.venc@ejemplo.com"]
        assert len(correos) == 1
        assert "vencimiento" in correos[0]["asunto"].lower()
        assert "Ana" in correos[0]["html"]

        # segunda corrida el mismo día: no duplica
        enviar_avisos_vencimientos(hoy)
        assert len([e for e in _sin_smtp_real
                    if e["destino"] == "contador.venc@ejemplo.com"]) == 1

        # a 3 días toca otro aviso distinto
        assert any(p["usuario"].email == "contador.venc@ejemplo.com"
                   for p in recordatorios_vencimientos(d(2026, 8, 9)))


def test_candado_diario_solo_un_worker(cliente, contador, _sin_smtp_real):
    from datetime import date as d
    from src.vencimientos import correr_avisos_diarios
    cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601", "obligaciones": ["renta_pn"]})
    hoy = d(2026, 8, 5)
    with webapp.app.app_context():
        VencimientoAviso.query.filter_by(usuario_id=0).delete()
        db.session.commit()
        try:
            n1 = correr_avisos_diarios(hoy)      # primer worker: envía
            n2 = correr_avisos_diarios(hoy)      # segundo: candado tomado
            assert n1 >= 1 and n2 == 0
        finally:
            VencimientoAviso.query.filter_by(usuario_id=0).delete()
            db.session.commit()


def test_recordatorios_ignora_no_pendientes(cliente, contador):
    from datetime import date as d
    cid = cliente.post("/api/vencimientos/clientes", json={
        "nombre": "Ana", "nit": "1030601",
        "obligaciones": ["renta_pn"]}).get_json()["cliente"]["id"]
    ev = cliente.get("/api/vencimientos/agenda").get_json()["eventos"][0]
    cliente.post("/api/vencimientos/estado", json={
        "cliente_id": cid, "clave": ev["clave"], "estado": "presentada"})
    with webapp.app.app_context():
        assert not any(p["usuario"].email == "contador.venc@ejemplo.com"
                       for p in recordatorios_vencimientos(d(2026, 8, 5)))
