"""Aplicación web local: arrastre la exógena y genere el Formulario 210.

Reutiliza el mismo motor que la CLI (src/): parser de exógena, mapeo,
motor de cálculo y escritura del Excel. Todo se procesa localmente.

Ejecutar:  .venv/bin/python webapp.py   →  http://127.0.0.1:5210
"""
import io
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import warnings
from datetime import date, datetime
from pathlib import Path

import yaml
from flask import (Flask, jsonify, redirect, render_template,
                   render_template_string, request, send_file, session, url_for)

from src import whatsapp as wa_mod
from src import wompi as wompi_mod
from src.asistente import asistente_activo as asistente_ia_activo
from src.asistente import cargar_config as cargar_config_ia
from src.asistente import responder as responder_ia
from src.auth import (AccesoAutorizado, ArchivoExogena, LeadEspera, MuestraContador,
                      OrdenRegistro, Usuario, auth_bp, autorizado_requerido, db,
                      init_auth, login_requerido, pro_requerido, usuario_actual)
from src.calendario import fecha_limite
from src.vencimientos import venc_bp
from src.documentos import generar_checklist_pdf
from src.guia_dian import generar_guia_dian_pdf

from src.entrevista import mapear_exogena_a_datos
from src.excel_writer import escribir_formulario
from src.exogena_parser import (ExogenaError, calcular_topes_propios,
                                evaluar_obligacion_declarar, parsear_exogena)
from src.modelos import DatosDeclaracion, ResultadoExogena
from src.motor_calculo import calcular
from src.parametros import Parametros
from src.firma import AVISO_LEGAL, FirmaError, firmar_pdf
from src.formulario_pdf import generar_formulario_pdf, sellar_formulario_pdf
from src.resumen_pdf import generar_resumen_pdf

BASE = Path(__file__).resolve().parent
PLANTILLA = BASE / "tests" / "fixtures" / "Plantilla renta naturales 2025 - ITGS.xlsx"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB

# Autenticación social (Google/Microsoft) + BD de usuarios
_OAUTH_CFG = init_auth(app)
app.register_blueprint(auth_bp)

# Gestor de vencimientos para contadores (gratis con login)
app.register_blueprint(venc_bp)


def _bucle_avisos_vencimientos():
    """Avisos diarios a contadores (7 y 3 días antes) sin cron externo: cada
    media hora mira si son las 8-9 a.m. de Bogotá y, con el candado en BD,
    un solo worker envía el lote del día."""
    from zoneinfo import ZoneInfo
    from src.vencimientos import correr_avisos_diarios

    time.sleep(120)                                # deja arrancar la app (y a pytest)
    while True:
        try:
            if not app.config.get("TESTING"):
                ahora = datetime.now(ZoneInfo("America/Bogota"))
                if 8 <= ahora.hour < 9:
                    with app.app_context():
                        n = correr_avisos_diarios(ahora.date())
                        if n:
                            print(f"[avisos-vencimientos] {n} correo(s) enviados")
        except Exception as exc:  # noqa: BLE001  — el hilo nunca debe morir
            print(f"[avisos-vencimientos] error: {exc}")
        time.sleep(1800)


threading.Thread(target=_bucle_avisos_vencimientos, daemon=True).start()


@app.context_processor
def _inyectar_usuario():
    """Deja disponible el usuario y los proveedores activos en todas las plantillas."""
    u = usuario_actual()
    g = _OAUTH_CFG.get("google", {})
    m = _OAUTH_CFG.get("microsoft", {})
    return {
        "usuario": u.to_dict() if u else None,
        "auth_google": bool(g.get("habilitado") and g.get("client_id")),
        "auth_microsoft": bool(m.get("habilitado") and m.get("client_id")),
        "auth_demo": bool(_OAUTH_CFG.get("demo_local")),
    }

# Exógenas cargadas en esta ejecución (memoria local, nunca sale de la máquina)
_EXOGENAS = {}

PARAMS = Parametros.cargar(2025)

ORDENES_PATH = BASE / "sessions" / "ordenes.json"
UPLOADS_DIR = BASE / "sessions" / "uploads"      # exógenas en espera de decisión
CLIENTES_DIR = BASE / "sessions" / "clientes"    # exógenas de trámites aceptados

GUIA_ARCHIVO = "guia-declarar-renta-2025.pdf"    # lead magnet en static/
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
with open(BASE / "config" / "precios.yaml", "r", encoding="utf-8") as _fh:
    _CFG_PRECIOS = yaml.safe_load(_fh)
    PLANES = _CFG_PRECIOS["planes"]
    PAGO = _CFG_PRECIOS.get("pago", {})
    # El WhatsApp de contacto es público (no secreto): vive en precios.yaml
    # dentro del repo para poder cambiarlo con un deploy, sin tocar Secret Files.
    _CONTACTO = _CFG_PRECIOS.get("contacto", {})
    URL_PUBLICA = str(_CONTACTO.get("sitio", "https://tributando.co")).rstrip("/")

_EPAYCO_PATH = BASE / "config" / "epayco.yaml"
EPAYCO = {"habilitado": False}
if _EPAYCO_PATH.exists():
    with open(_EPAYCO_PATH, "r", encoding="utf-8") as _fh:
        EPAYCO = yaml.safe_load(_fh) or EPAYCO

_REALMY_PATH = BASE / "config" / "realmy.yaml"
REALMY = {"habilitado": False}
if _REALMY_PATH.exists():
    with open(_REALMY_PATH, "r", encoding="utf-8") as _fh:
        REALMY = yaml.safe_load(_fh) or REALMY

IA_CFG = cargar_config_ia()
# Si precios.yaml (repo) define un WhatsApp de contacto, manda sobre el del
# Secret File ia.yaml. Así el número público se cambia con un git push y lo
# usan a la vez landing, /contabilidad, /links, la guía PDF y el asistente.
if _CONTACTO.get("whatsapp"):
    IA_CFG.setdefault("negocio", {})["whatsapp"] = _CONTACTO["whatsapp"]
WOMPI = wompi_mod.cargar_config()


def _leer_ordenes() -> dict:
    """Todas las órdenes/cargas como dict {id: registro}, desde la BD."""
    return {fila.id: json.loads(fila.data) for fila in OrdenRegistro.query.all()}


def _guardar_ordenes(ordenes: dict) -> None:
    """Sincroniza la BD con el dict completo (upsert + borrado de faltantes).

    Conserva la semántica que tenía el archivo ordenes.json: quien llama lee el
    dict entero, lo modifica y lo vuelve a guardar; borrar una clave del dict
    la elimina también del almacenamiento.
    """
    try:
        existentes = {fila.id: fila for fila in OrdenRegistro.query.all()}
        for oid, registro in ordenes.items():
            blob = json.dumps(registro, ensure_ascii=False, default=str)
            fila = existentes.pop(oid, None)
            if fila is None:
                db.session.add(OrdenRegistro(id=oid, data=blob))
            elif fila.data != blob:
                fila.data = blob
        for fila in existentes.values():   # claves borradas del dict
            db.session.delete(fila)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def _guardar_archivo_bd(clave: str, nombre: str, datos: bytes) -> None:
    """Guarda (o reemplaza) un Excel de exógena en la BD bajo la clave dada."""
    try:
        fila = db.session.get(ArchivoExogena, clave)
        if fila is None:
            db.session.add(ArchivoExogena(id=clave, nombre=nombre, datos=datos))
        else:
            fila.nombre, fila.datos = nombre, datos
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error("No se pudo guardar el Excel %s en la BD: %s", clave, e)


def _leer_archivo_bd(clave: str):
    """Devuelve la fila ArchivoExogena o None."""
    return db.session.get(ArchivoExogena, clave)


# Migración única: si la BD está vacía y existe el ordenes.json viejo, se importa
# para no perder lo que hubiera en el disco (órdenes de prueba, cargas activas).
with app.app_context():
    try:
        if ORDENES_PATH.exists() and OrdenRegistro.query.first() is None:
            with open(ORDENES_PATH, "r", encoding="utf-8") as fh:
                _viejas = json.load(fh)
            for _oid, _reg in _viejas.items():
                db.session.add(OrdenRegistro(
                    id=_oid, data=json.dumps(_reg, ensure_ascii=False, default=str)))
            db.session.commit()
            app.logger.info("ordenes.json importado a la BD: %d registros", len(_viejas))
    except Exception as _e:
        db.session.rollback()
        app.logger.warning("No se pudo importar ordenes.json: %s", _e)


@app.get("/api/salud")
def salud():
    """Chequeo de salud: confirma que la app responde y contra qué base corre.

    Solo expone el nombre del motor y si la conexión vive; nunca credenciales,
    host ni nombre de la base. Sirve para verificar tras un despliegue que
    producción quedó apuntando a Postgres y no a un SQLite efímero.
    """
    from sqlalchemy import text

    try:
        db.session.execute(text("SELECT 1"))
        conectada = True
    except Exception:
        conectada = False
    # Solo booleanos de configuración, nunca credenciales ni direcciones.
    from src.correo import cargar_config_email
    return jsonify({
        "ok": conectada,
        "motor": db.engine.dialect.name,
        "anio_gravable": PARAMS.anio_gravable,
        "correo": bool(cargar_config_email().get("habilitado")),
        "asistente": asistente_ia_activo(IA_CFG),
    }), (200 if conectada else 503)


@app.get("/")
def landing():
    # Todo el contenido de la landing es visible sin iniciar sesión; solo las
    # interacciones (subir exógena, checkout) piden login — ver JS abajo y los
    # decoradores @login_requerido en las rutas /api/* correspondientes. El chat
    # de soporte es libre: solo tiene límite de mensajes por IP.
    return render_template("landing.html", anio=PARAMS.anio_gravable,
                           planes=PLANES, realmy_habilitado=REALMY.get("habilitado"),
                           realmy_public_key=REALMY.get("public_key", ""),
                           realmy_merchant_id=REALMY.get("merchant_id", ""),
                           realmy_test=REALMY.get("test", True),
                           ia_habilitado=asistente_ia_activo(IA_CFG),
                           ia_nombre=IA_CFG.get("nombre_asistente", "Asistente"),
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""),
                           ia_correo=IA_CFG.get("negocio", {}).get("correo", ""),
                           wompi_habilitado=wompi_mod.activo(WOMPI),
                           usuario_logueado=usuario_actual() is not None)


# El chat de soporte es LIBRE (sin login): es el primer punto de contacto de un
# cliente potencial, que debe poder preguntar antes de registrarse. Un límite de
# mensajes por IP protege la cuota gratuita de Gemini contra abusos.
_CHAT_VENTANA = 10 * 60      # segundos
_CHAT_MAX_POR_IP = 20        # mensajes por IP dentro de la ventana
_chat_ips: dict = {}
_chat_lock = threading.Lock()


def _chat_permitido(ip: str) -> bool:
    ahora = time.time()
    with _chat_lock:
        marcas = [t for t in _chat_ips.get(ip, ()) if ahora - t < _CHAT_VENTANA]
        if len(marcas) >= _CHAT_MAX_POR_IP:
            _chat_ips[ip] = marcas
            return False
        marcas.append(ahora)
        _chat_ips[ip] = marcas
        return True


def _ip_cliente() -> str:
    # Detrás del proxy de Render la IP real viaja en X-Forwarded-For.
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() or request.remote_addr or "?")


@app.post("/api/chat")
def api_chat():
    """Responde una duda del cliente con el asistente de IA (sin exigir login)."""
    if not asistente_ia_activo(IA_CFG):
        return jsonify({"error": "El asistente no está disponible."}), 503
    if not _chat_permitido(_ip_cliente()):
        return jsonify({"error": "Has enviado muchos mensajes seguidos. "
                                 "Espera unos minutos e inténtalo de nuevo. 🙏"}), 429
    cuerpo = request.get_json(silent=True) or {}
    mensajes = cuerpo.get("mensajes")
    if not isinstance(mensajes, list) or not mensajes:
        return jsonify({"error": "Envía al menos un mensaje."}), 400

    # Si el cliente ya tiene su declaración en pantalla, el asistente responde
    # conociendo cómo quedó la liquidación en vez de dar respuestas genéricas.
    liq = None
    if isinstance(cuerpo.get("datos"), dict):
        try:
            liq = calcular(DatosDeclaracion.from_dict(cuerpo["datos"]), PARAMS)
        except (TypeError, KeyError, ValueError):
            liq = None                # datos incompletos: se responde sin contexto

    try:
        respuesta = responder_ia(mensajes, IA_CFG, usuario=usuario_actual(), liq=liq)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:                       # nunca tumbar el chat por un fallo de la API
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            return jsonify({"error": "Estoy atendiendo muchas consultas en este momento. "
                                     "Espera unos segundos e inténtalo de nuevo. 🙏"}), 429
        app.logger.warning("Fallo del asistente de IA: %s", e)
        return jsonify({"error": "No pude responder ahora mismo. Intenta de nuevo en un momento."}), 502
    return jsonify({"respuesta": respuesta})


# --- WhatsApp Cloud API: el asistente responde también por WhatsApp ---------
# Meta valida el webhook con un GET (handshake) y entrega los mensajes con un
# POST. El POST debe responder 200 rápido o Meta reintenta; por eso cualquier
# fallo se traga y siempre devolvemos 200.
@app.get("/api/whatsapp")
def whatsapp_verificar():
    challenge = wa_mod.verificar_webhook(
        IA_CFG,
        request.args.get("hub.mode", ""),
        request.args.get("hub.verify_token", ""),
        request.args.get("hub.challenge", ""),
    )
    if challenge is None:
        return "forbidden", 403
    return challenge, 200


@app.post("/api/whatsapp")
def whatsapp_webhook():
    payload = request.get_json(silent=True) or {}
    if wa_mod.activo(IA_CFG) and asistente_ia_activo(IA_CFG):
        wa_mod.atender(IA_CFG, payload, lambda hist: responder_ia(hist, IA_CFG))
    return "ok", 200


@app.post("/api/guia")
def api_guia():
    """Captura el correo para la lista de espera y entrega la guía-obsequio.

    Guarda el correo (dedup) para avisarle a la persona cuando la DIAN habilite
    la exógena, y devuelve el enlace de descarga del PDF. El correo es opcional
    para el negocio pero es lo que convierte la visita en un contacto.
    """
    if not _chat_permitido(_ip_cliente()):        # mismo antiabuso por IP que el chat
        return jsonify({"error": "Demasiados intentos. Espera unos minutos. 🙏"}), 429
    cuerpo = request.get_json(silent=True) or {}
    email = (cuerpo.get("email") or "").strip().lower()
    nombre = (cuerpo.get("nombre") or "").strip()[:80]
    if not _EMAIL_RE.match(email) or len(email) > 120:
        return jsonify({"error": "Escribe un correo válido para enviarte la guía."}), 400
    # Guarda el lead en Postgres (no en el filesystem, que Render borra en cada
    # despliegue). Si falla el guardado, igual entregamos la guía: la descarga
    # del cliente no debe depender de un tropiezo de la BD.
    try:
        if not LeadEspera.query.filter_by(email=email).first():
            db.session.add(LeadEspera(email=email, nombre=nombre, ip=_ip_cliente()))
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.warning("No se pudo guardar el lead %s: %s", email, e)
    return jsonify({"ok": True, "url": url_for("static", filename=GUIA_ARCHIVO)})


@app.get("/contabilidad")
def contabilidad():
    """Página del servicio de contabilidad para negocios (cross-sell)."""
    return render_template("contabilidad.html",
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""))


@app.get("/links")
@app.get("/enlaces")
def enlaces():
    """Página 'link en la bio': reúne todos los servicios en botones para redes."""
    return render_template("enlaces.html",
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""))


@app.get("/contadores")
def contadores():
    """Página mayorista para contadores: pase de temporada (venta por WhatsApp)
    + prueba gratis de 1 declaración de muestra. El acceso pago se habilita
    agregando el correo del contador a config/acceso.yaml."""
    u = usuario_actual()
    muestra_usada = False
    if u is not None:
        muestra_usada = db.session.get(MuestraContador, u.id) is not None
    return render_template("contadores.html",
                           contadores=_CFG_PRECIOS.get("contadores", {}),
                           logueado=u is not None,
                           muestra_usada=muestra_usada,
                           pago=PAGO,
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""))


@app.post("/api/pase-contador/crear")
@login_requerido
def crear_pase_contador():
    """Crea la orden del pase de temporada de un contador (sin exógena). Usa el
    correo del usuario logueado, que es con el que se le habilita al confirmar."""
    u = usuario_actual()
    cont = _CFG_PRECIOS.get("contadores", {})
    cuerpo = request.get_json(silent=True) or {}
    contacto = {"email": (u.email or "").strip(), "nombre": (u.nombre or "").strip(),
                "telefono": str(cuerpo.get("telefono", "")).strip()}
    if not contacto["email"]:
        return jsonify({"error": "Tu cuenta no tiene correo; usa otra o escríbenos."}), 400
    orden_id = uuid.uuid4().hex[:12]
    ordenes = _leer_ordenes()
    ordenes[orden_id] = {
        "tipo": "orden", "plan": "contadores",
        "precio": cont.get("precio", 249000), "contacto": contacto,
        "estado": "pendiente_pago", "fecha": str(date.today()),
        "nit": "", "nombre": (u.nombre or "Contador"),
    }
    _guardar_ordenes(ordenes)
    return jsonify({"orden_id": orden_id, "precio": cont.get("precio", 249000)})


@app.get("/api/muestra-contador/<token>.pdf")
@login_requerido
def muestra_contador_pdf(token):
    """Entrega UNA vez, gratis, el Formulario 210 de MUESTRA (con marca de agua)
    a un contador que se registró. El límite (1 por usuario) vive en la BD."""
    u = usuario_actual()
    ordenes = _leer_ordenes()
    carga = ordenes.get(token)
    if not carga or carga.get("tipo") != "carga":
        return jsonify({"error": "Sube primero una exógena."}), 400
    try:
        datos = DatosDeclaracion.from_dict(carga.get("datos", {}))
    except (TypeError, KeyError):
        return jsonify({"error": "No hay datos válidos para la muestra."}), 410

    previa = db.session.get(MuestraContador, u.id)
    if previa is not None and previa.token != token:
        return jsonify({"error": "Ya usaste tu declaración de muestra gratis. "
                        "Activa tu pase de temporada para ilimitadas."}), 402

    liq = calcular(datos, PARAMS)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_formulario_pdf(salida, datos, liq, PARAMS, marca="MUESTRA · TRIBUTANDO.CO")
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    if previa is None:
        try:
            db.session.add(MuestraContador(usuario_id=u.id, email=u.email,
                                           token=token, nit_muestra=carga.get("nit", "")))
            db.session.commit()
        except Exception:
            db.session.rollback()   # si falla el registro, igual entregamos la muestra

    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name=f"MUESTRA_Formulario210_{carga.get('nit','')}.pdf",
                     mimetype="application/pdf")


@app.get("/guia-dian")
def guia_dian_web():
    """Versión web (HTML) de la guía para presentar el Formulario 210 en la DIAN.
    Complementa el PDF: se ve bien en el celular y se comparte con un link."""
    return render_template("guia_dian.html",
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""))


@app.get("/liquidador")
@pro_requerido
def index():
    return render_template("index.html", anio=PARAMS.anio_gravable, uvt=PARAMS.uvt)


@app.post("/api/cargar")
@pro_requerido
def cargar():
    """Recibe el .xlsx arrastrado, lo parsea y devuelve datos + resumen."""
    archivo = request.files.get("exogena")
    if archivo is None or archivo.filename == "":
        return jsonify({"error": "No llegó ningún archivo."}), 400
    if not archivo.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"error": "El archivo debe ser .xlsx (reporte de exógena DIAN)."}), 400

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        archivo.save(tmp.name)
        ruta_tmp = Path(tmp.name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exogena = parsear_exogena(ruta_tmp)
    except ExogenaError as exc:
        return jsonify({"error": str(exc)}), 422
    finally:
        ruta_tmp.unlink(missing_ok=True)

    token = uuid.uuid4().hex
    _EXOGENAS[token] = exogena
    datos = mapear_exogena_a_datos(exogena, PARAMS)

    topes = exogena.topes_dian or calcular_topes_propios(exogena)
    return jsonify({
        "token": token,
        "datos": datos.to_dict(),
        "resumen": {
            "nombre": exogena.nombre,
            "identificacion": exogena.identificacion,
            "anio": exogena.anio,
            "num_partidas": len(exogena.partidas),
            "topes": topes,
            "obligado": evaluar_obligacion_declarar(topes, PARAMS),
            "advertencias": exogena.advertencias,
            "partidas": [
                {
                    "fila": p.fila,
                    "renglon": p.renglon_asignado,
                    "detalle": p.detalle,
                    "informante": p.informante_nombre,
                    "valor": p.valor,
                    "nota": p.nota,
                }
                for p in exogena.partidas
            ],
        },
    })


@app.post("/api/calcular")
@pro_requerido
def calcular_api():
    """Recibe los datos (posiblemente editados) y devuelve la liquidación."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    liq = calcular(datos, PARAMS)
    return jsonify({
        "renglones": {str(k): v for k, v in sorted(liq.renglones.items())},
        "advertencias": liq.advertencias,
        "detalle": liq.detalle,
    })


@app.post("/api/generar")
@pro_requerido
def generar():
    """Genera y descarga el Excel del Formulario 210 con los datos editados."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    exogena = _EXOGENAS.get(cuerpo.get("token", ""))
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            escribir_formulario(PLANTILLA, salida, datos, liq, exogena)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name=f"Formulario210_{nit}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/resumen-pdf")
@pro_requerido
def resumen_pdf():
    """Genera y descarga el resumen ejecutivo en PDF."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    exogena = _EXOGENAS.get(cuerpo.get("token", ""))
    liq = calcular(datos, PARAMS)
    razones = []
    if exogena is not None:
        topes = exogena.topes_dian or calcular_topes_propios(exogena)
        razones = evaluar_obligacion_declarar(topes, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_resumen_pdf(salida, datos, liq, PARAMS, exogena, razones)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name=f"ResumenEjecutivo_Renta_{nit}.pdf",
        mimetype="application/pdf",
    )


# ======================================================================
# Landing comercial: verificación + valor a pagar + planes con pago
# ======================================================================

@app.post("/api/cargar-landing")
@login_requerido
def cargar_landing():
    """Sube la exógena y devuelve SOLO el resultado comercial:
    obligación de declarar, fecha límite y valor a pagar estimado.
    El detalle de la liquidación no se expone (hace parte del servicio pago)."""
    archivo = request.files.get("exogena")
    if archivo is None or archivo.filename == "":
        return jsonify({"error": "No llegó ningún archivo."}), 400
    if not archivo.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"error": "El archivo debe ser el Excel (.xlsx) de la exógena DIAN."}), 400

    token = uuid.uuid4().hex
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ruta_upload = UPLOADS_DIR / f"{token}.xlsx"
    archivo.save(ruta_upload)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            exogena = parsear_exogena(ruta_upload)
    except ExogenaError as exc:
        ruta_upload.unlink(missing_ok=True)
        return jsonify({"error": str(exc)}), 422

    datos = mapear_exogena_a_datos(exogena, PARAMS)
    liq = calcular(datos, PARAMS)
    topes = exogena.topes_dian or calcular_topes_propios(exogena)
    razones = evaluar_obligacion_declarar(topes, PARAMS)
    limite = fecha_limite(exogena.identificacion, PLANTILLA)
    dias = (limite - date.today()).days if limite else None

    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    fecha_texto = f"{limite.day} de {meses[limite.month]} de {limite.year}" if limite else None

    _EXOGENAS[token] = exogena
    ordenes = _leer_ordenes()
    ordenes[token] = {"tipo": "carga", "datos": datos.to_dict(),
                      "nombre": exogena.nombre, "nit": exogena.identificacion,
                      "archivo": str(ruta_upload),
                      "fecha_carga": str(date.today())}
    _guardar_ordenes(ordenes)
    # El Excel también va a la BD: el disco local es efímero en Render y este
    # archivo es el insumo del trámite si el cliente luego paga presentación.
    _guardar_archivo_bd(token, f"Exogena_{exogena.identificacion or 'sin_nit'}.xlsx",
                        ruta_upload.read_bytes())

    primer_nombre = (exogena.nombre or "").split()[-1].title() if exogena.nombre else ""
    return jsonify({
        "token": token,
        "nombre": primer_nombre,
        "nit_final": (exogena.identificacion or "")[-2:],
        "obligado": bool(razones),
        "razones": razones,
        "fecha_limite": fecha_texto,
        "fecha_limite_iso": str(limite) if limite else None,
        "dias_restantes": dias,
        "valor_a_pagar": liq.r(136),
        "saldo_a_favor": liq.r(137),
        "patrimonio_bruto": datos.patrimonio_bruto,
        "deudas": datos.deudas,
    })


def _monto_valido(valor) -> float:
    """Convierte un monto del cliente a float sano (0 .. 1 billón de billones no)."""
    monto = float(valor)
    if monto < 0 or monto > 1e13:
        raise ValueError(valor)
    return monto


@app.post("/api/recalcular-landing")
@login_requerido
def recalcular_landing():
    """Recalcula el estimado al ajustar dependientes y/o patrimonio (R29/R30).
    Cada campo es opcional y lo que no venga conserva su valor guardado, para
    que corregir el patrimonio no borre los dependientes ya elegidos (y al
    revés). Todo queda guardado para que el PDF pagado salga con esos datos."""
    cuerpo = request.get_json(silent=True) or {}
    token = cuerpo.get("token", "")
    ordenes = _leer_ordenes()
    if token not in ordenes or ordenes[token].get("tipo") != "carga":
        return jsonify({"error": "Cargue primero su archivo de exógena."}), 400

    datos = DatosDeclaracion.from_dict(ordenes[token]["datos"])
    if "dependientes" in cuerpo:
        try:
            dependientes = max(0, min(int(cuerpo["dependientes"]), 10))
        except (TypeError, ValueError):
            return jsonify({"error": "Número de dependientes inválido."}), 400
        datos.dependientes = dependientes
        datos.dependientes_detalle = [f"Dependiente {i+1}"
                                      for i in range(min(dependientes, 4))]
    if "patrimonio_bruto" in cuerpo:
        try:
            datos.patrimonio_bruto = _monto_valido(cuerpo["patrimonio_bruto"])
        except (TypeError, ValueError):
            return jsonify({"error": "Patrimonio inválido."}), 400
    if "deudas" in cuerpo:
        try:
            datos.deudas = _monto_valido(cuerpo["deudas"])
        except (TypeError, ValueError):
            return jsonify({"error": "Valor de deudas inválido."}), 400

    # El "ahorro" por dependientes se calcula contra el mismo escenario sin ellos.
    dependientes_elegidos = datos.dependientes
    detalle_elegido = list(datos.dependientes_detalle)
    datos.dependientes, datos.dependientes_detalle = 0, []
    sin_dep = calcular(datos, PARAMS)
    datos.dependientes, datos.dependientes_detalle = dependientes_elegidos, detalle_elegido
    liq = calcular(datos, PARAMS)

    ordenes[token]["datos"] = datos.to_dict()
    _guardar_ordenes(ordenes)
    return jsonify({
        "dependientes": datos.dependientes,
        "patrimonio_bruto": datos.patrimonio_bruto,
        "deudas": datos.deudas,
        "valor_a_pagar": liq.r(136),
        "saldo_a_favor": liq.r(137),
        "ahorro": max(0.0, (sin_dep.r(136) - sin_dep.r(137))
                      - (liq.r(136) - liq.r(137))),
    })


@app.post("/api/checkout")
@login_requerido
def checkout():
    """Crea la orden de un plan. El pago real requiere pasarela (pendiente):
    aquí se simula para probar el flujo completo."""
    cuerpo = request.get_json(silent=True) or {}
    token = cuerpo.get("token", "")
    plan = cuerpo.get("plan", "")
    contacto = cuerpo.get("contacto") or {}
    ordenes = _leer_ordenes()
    if token not in ordenes:
        return jsonify({"error": "Cargue primero su archivo de exógena."}), 400
    if plan not in PLANES:
        return jsonify({"error": f"Plan desconocido: {plan}"}), 400
    if not contacto.get("email") and not contacto.get("telefono"):
        return jsonify({"error": "Déjenos un correo o teléfono de contacto."}), 400

    orden_id = uuid.uuid4().hex[:12]
    ordenes[orden_id] = {
        "tipo": "orden", "token": token, "plan": plan,
        "precio": PLANES[plan]["precio"], "contacto": contacto,
        "estado": "pendiente_pago", "fecha": str(date.today()),
        "nit": ordenes[token].get("nit", ""), "nombre": ordenes[token].get("nombre", ""),
    }
    _guardar_ordenes(ordenes)
    return jsonify({"orden_id": orden_id, "plan": PLANES[plan],
                    "precio": PLANES[plan]["precio"], "pago": PAGO})


@app.post("/api/checkout-realmy")
@login_requerido
def checkout_realmy():
    """Genera un token para procesar pago con Realmy.
    Realmy está habilitado en config/realmy.yaml."""
    if not REALMY.get("habilitado"):
        return jsonify({"error": "Realmy no está habilitado."}), 400

    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404

    # Datos para el checkout de Realmy
    precio = orden.get("precio", 0)
    nit = orden.get("nit", "")
    nombre = orden.get("nombre", "")
    plan = orden.get("plan", "")

    return jsonify({
        "status": "ok",
        "orden_id": orden_id,
        "precio": precio,
        "nit": nit,
        "nombre": nombre,
        "plan": plan,
        "public_key": REALMY.get("public_key", ""),
        "merchant_id": REALMY.get("merchant_id", ""),
        "test_mode": REALMY.get("test", True),
        "referencia": f"RENTA-{orden_id.upper()[:12]}",
    })


@app.post("/api/reportar-pago")
@login_requerido
def reportar_pago():
    """El cliente informa que ya hizo la consignación/transferencia."""
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    if orden["estado"] == "pendiente_pago":
        orden["estado"] = "pago_reportado"
        # Aviso al negocio: hay una consignación por verificar. Nunca tumba el
        # endpoint (la orden queda en el panel /admin de todos modos).
        from src.correo import notificar_pago
        if notificar_pago(orden_id, orden, confirmado=False):
            orden["aviso_pago_enviado"] = True
        _guardar_ordenes(ordenes)
    return jsonify({"estado": orden["estado"], "orden_id": orden_id})


def _entregar_pdf_al_cliente(orden_id: str, orden: dict, ordenes: dict) -> None:
    """Envía al cliente su Formulario 210 y la guía por correo, con links de
    descarga. Solo para el plan PDF (en presentación lo hacemos nosotros).
    Idempotente: no reenvía si ya se entregó. No lanza excepción."""
    if orden.get("plan") != "pdf" or orden.get("entrega_cliente_enviada"):
        return
    email = (orden.get("contacto") or {}).get("email", "").strip()
    if not email:
        return
    try:
        from src.correo import cargar_config_email, enviar_email
        cfg = cargar_config_email()
        if not cfg.get("habilitado"):
            return

        carga = ordenes.get(orden.get("token", ""), {})
        datos = DatosDeclaracion.from_dict(carga.get("datos", {}))
        liq = calcular(datos, PARAMS)
        limite = fecha_limite(carga.get("nit", orden.get("nit", "")), PLANTILLA)
        nombre = carga.get("nombre", orden.get("nombre", ""))
        nit = orden.get("nit", "")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            p_form = Path(tf.name)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tg:
            p_guia = Path(tg.name)
        try:
            generar_formulario_pdf(p_form, datos, liq, PARAMS)
            generar_guia_dian_pdf(p_guia, nombre=nombre,
                                  fecha_limite=str(limite) if limite else None)
            adj_form = p_form.read_bytes()
            adj_guia = p_guia.read_bytes()
        finally:
            p_form.unlink(missing_ok=True)
            p_guia.unlink(missing_ok=True)

        primer = (nombre or "").split()[0] if nombre else ""
        saludo = f"Hola {primer}," if primer else "Hola,"
        link_form = f"{URL_PUBLICA}/api/orden/{orden_id}/formulario.pdf"
        link_guia = f"{URL_PUBLICA}/api/orden/{orden_id}/guia-dian.pdf"
        azul, dorado = "#123f6b", "#cdab7e"
        html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f5f7fa;
          font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1e2b3a">
          <div style="max-width:560px;margin:0 auto;padding:24px">
            <div style="background:#fff;border-radius:16px;overflow:hidden;
              box-shadow:0 6px 20px rgba(18,63,107,.08)">
              <div style="background:{azul};color:#fff;padding:22px 26px">
                <div style="font-size:1.4rem">🧾</div>
                <div style="font-size:1.15rem;font-weight:700;margin-top:6px">
                  Tu declaración de renta está lista</div>
              </div>
              <div style="padding:24px 26px;font-size:.95rem;line-height:1.6">
                <p>{saludo}</p>
                <p>¡Gracias por confiar en Tributando.co! Adjunto a este correo
                  encuentras <b>dos documentos</b>:</p>
                <ul style="padding-left:18px">
                  <li><b>Formulario 210 (borrador)</b> — tu declaración diligenciada
                    renglón por renglón.</li>
                  <li><b>Guía para presentarla en la DIAN</b> — el paso a paso para
                    que la subas tú mismo al portal.</li>
                </ul>
                <p style="margin-top:6px">También puedes descargarlos desde tu cuenta:</p>
                <p style="text-align:center;margin:20px 0">
                  <a href="{link_form}" style="background:{dorado};color:{azul};
                    text-decoration:none;padding:12px 22px;border-radius:10px;
                    font-weight:700;display:inline-block;margin:4px">Descargar Formulario 210</a>
                  <a href="{link_guia}" style="background:#eef2f7;color:{azul};
                    text-decoration:none;padding:12px 22px;border-radius:10px;
                    font-weight:700;display:inline-block;margin:4px">Descargar la guía</a>
                </p>
                <p style="font-size:.85rem;color:#5a6b7f">Recuerda: con este plan
                  <b>tú presentas</b> la declaración en la DIAN siguiendo la guía. Si
                  prefieres que la presentemos por ti, responde este correo y te
                  ayudamos.</p>
              </div>
              <div style="padding:16px 26px;border-top:1px solid #eef2f7;
                font-size:.72rem;color:#9db0c4">
                Orden {orden_id.upper()}{f" · NIT/Cédula termina en {str(nit)[-4:]}" if nit else ""}
                · Tributando.co
              </div>
            </div>
          </div></body></html>"""
        asunto = "🧾 Tu Formulario 210 y la guía para presentarlo — Tributando.co"
        enviar_email(email, asunto, html, cfg, adjuntos=[
            (f"Formulario210_{nit or 'borrador'}.pdf", adj_form, "application/pdf"),
            ("Guia_presentar_declaracion_DIAN.pdf", adj_guia, "application/pdf"),
        ])
        orden["entrega_cliente_enviada"] = True
    except Exception as e:
        app.logger.warning("No se pudo entregar el PDF al cliente de la orden %s: %s",
                           orden_id, e)


def _entregar_pase_contador(orden_id: str, orden: dict) -> None:
    """Correo de bienvenida del pase de temporada: le dice al contador que su
    acceso al liquidador quedó habilitado y cómo entrar. Idempotente (bandera
    bienvenida_pase_enviada). Nunca lanza excepción."""
    if orden.get("plan") != "contadores" or orden.get("bienvenida_pase_enviada"):
        return
    email = (orden.get("contacto") or {}).get("email", "").strip()
    if not email:
        return
    try:
        from src.correo import cargar_config_email, enviar_email
        cfg = cargar_config_email()
        if not cfg.get("habilitado"):
            return
        cont = _CFG_PRECIOS.get("contadores", {})
        sitio = (_CONTACTO.get("sitio") or "https://tributando.co").rstrip("/")
        wa = re.sub(r"\D", "", str(_CONTACTO.get("whatsapp", "")))
        nombre = ((orden.get("contacto") or {}).get("nombre", "")
                  or orden.get("nombre", ""))
        primer = nombre.split()[0].title() if nombre else ""
        saludo = f"Hola {primer}," if primer else "Hola,"
        navy, dorado = "#1e2432", "#b8955f"
        html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f5f7fa;
          font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1e2b3a">
          <div style="max-width:560px;margin:0 auto;padding:24px">
            <div style="background:#fff;border-radius:16px;overflow:hidden;
              box-shadow:0 6px 20px rgba(18,63,107,.08)">
              <div style="background:{navy};color:#fff;padding:24px 26px">
                <div style="font-size:1.5rem">👔</div>
                <div style="font-size:1.2rem;font-weight:800;margin-top:6px">
                  Tu pase de temporada está <span style="color:{dorado}">activo</span></div>
              </div>
              <div style="padding:24px 26px;font-size:.95rem;line-height:1.65">
                <p>{saludo}</p>
                <p>Confirmamos tu pago del <b>{cont.get('nombre', 'Pase de temporada')}</b>
                  (orden <code>{orden_id}</code>). Tu acceso al <b>liquidador profesional</b>
                  ya quedó habilitado con este correo (<b>{email}</b>).</p>
                <div style="background:#f5f7fa;border-radius:12px;padding:18px 20px;margin:18px 0">
                  <b>Para empezar:</b>
                  <ol style="margin:10px 0 0 18px;padding:0">
                    <li>Entra a <a href="{sitio}/liquidador">{sitio.replace('https://','')}/liquidador</a></li>
                    <li>Inicia sesión con <b>Google o Microsoft</b> usando <b>este mismo correo</b></li>
                    <li>Sube la exógena de tu cliente y descarga su Formulario 210</li>
                  </ol>
                </div>
                <p><b>Tu pase incluye</b> (temporada {cont.get('temporada', '')}):</p>
                <ul style="margin:8px 0 0 18px;padding:0">
                  <li>Declaraciones <b>ilimitadas</b></li>
                  <li>Formulario 210 en PDF y Excel con papeles de trabajo</li>
                  <li>Anexo del cruce exógena → 210 (NIT por NIT) y topes evaluados</li>
                  <li>Soporte directo por WhatsApp</li>
                </ul>
                <p style="text-align:center;margin:24px 0 8px">
                  <a href="{sitio}/liquidador" style="background:{dorado};color:#fff;
                    text-decoration:none;padding:13px 28px;border-radius:10px;
                    font-weight:700;display:inline-block">Entrar al liquidador</a></p>
                <p style="font-size:.82rem;color:#5a6b7f;text-align:center">
                  ¿Dudas? Escríbenos por <a href="https://wa.me/{wa}">WhatsApp</a>
                  o responde este correo.</p>
              </div>
              <div style="padding:16px 26px;border-top:1px solid #eef2f7;font-size:.72rem;color:#9db0c4">
                Tributando.co · herramienta profesional para contadores</div>
            </div>
          </div></body></html>"""
        enviar_email(email, "✅ Tu pase de temporada está activo — liquidador habilitado",
                     html, cfg)
        orden["bienvenida_pase_enviada"] = True
    except Exception:
        pass


def _finalizar_pago_orden(orden_id: str, orden: dict, ordenes: dict) -> None:
    """Marca la orden como pagada y, si es plan de presentación, conserva la
    exógena y genera el checklist para el trámite. Idempotente."""
    orden["estado"] = ("pagada" if orden["plan"] in ("pdf", "contadores")
                       else "pagada_en_tramite")

    # Pase de contadores: al confirmar el pago se habilita SOLO el acceso al
    # liquidador (usando el correo con que el contador entró y compró el pase).
    if orden["plan"] == "contadores":
        email = (orden.get("contacto") or {}).get("email", "").strip().lower()
        if email and db.session.get(AccesoAutorizado, email) is None:
            try:
                db.session.add(AccesoAutorizado(
                    email=email, nombre=(orden.get("contacto") or {}).get("nombre", ""),
                    nota="Pase de temporada (pago confirmado)"))
                db.session.commit()
            except Exception:
                db.session.rollback()
        # correo de bienvenida con el acceso (idempotente)
        _entregar_pase_contador(orden_id, orden)

    # Aviso al negocio de que entró dinero confirmado. La bandera evita
    # reenviarlo cuando la pasarela repite el webhook (esta función es
    # idempotente y puede ejecutarse más de una vez por orden).
    if not orden.get("aviso_pago_confirmado_enviado"):
        from src.correo import notificar_pago
        if notificar_pago(orden_id, orden, confirmado=True):
            orden["aviso_pago_confirmado_enviado"] = True

    # Entrega automática al cliente (plan PDF): Formulario 210 + guía + links.
    _entregar_pdf_al_cliente(orden_id, orden, ordenes)

    # plan recomendado aceptado: se conserva la exógena para hacer el trámite
    if orden["plan"] == "presentacion":
        carga = ordenes.get(orden.get("token", ""), {})
        origen = Path(carga.get("archivo", ""))
        if origen.exists():
            CLIENTES_DIR.mkdir(parents=True, exist_ok=True)
            destino = CLIENTES_DIR / f"{orden_id}_Exogena_{carga.get('nit','')}.xlsx"
            shutil.copy2(origen, destino)
            orden["archivo_cliente"] = str(destino)
        # copia ligada a la orden en la BD (sobrevive redeploys aunque el disco no)
        fila_x = _leer_archivo_bd(orden.get("token", ""))
        datos_x = fila_x.datos if fila_x else (origen.read_bytes() if origen.exists() else None)
        if datos_x:
            _guardar_archivo_bd(f"orden:{orden_id}",
                                f"Exogena_{carga.get('nit','')}.xlsx", datos_x)
        # checklist de documentos junto al trámite, para control interno
        try:
            limite = fecha_limite(carga.get("nit", ""), PLANTILLA)
            generar_checklist_pdf(
                CLIENTES_DIR / f"{orden_id}_Documentos_{carga.get('nit','')}.pdf",
                nombre=carga.get("nombre", ""),
                fecha_limite=str(limite) if limite else None)
        except Exception:
            pass


@app.post("/api/confirmar-pago")
@autorizado_requerido
def confirmar_pago():
    """Confirmación del pago (panel admin, tras verificar la consignación)."""
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    _finalizar_pago_orden(orden_id, orden, ordenes)
    _guardar_ordenes(ordenes)
    return jsonify({"estado": orden["estado"], "orden_id": orden_id})


@app.post("/api/realmy-webhook")
def realmy_webhook():
    """Webhook de Realmy: confirma un pago completado.

    Realmy envía una notificación POST con los detalles de la transacción.
    Validamos la firma y actualizamos el estado de la orden.

    Registra esta URL en el dashboard de Realmy:
    https://tu-dominio.com/api/realmy-webhook
    """
    import hmac
    import hashlib

    if not REALMY.get("habilitado"):
        return jsonify({"error": "Realmy no habilitado."}), 400

    cuerpo = request.get_json(silent=True) or {}

    # Validar firma si está disponible el secret
    webhook_secret = REALMY.get("webhook_secret", "")
    if webhook_secret:
        firma_recibida = cuerpo.get("signature", "")
        # Realmy típicamente envía x = dato1,dato2,dato3... y signature = HMAC-SHA256
        # Aquí se simplifica; ajusta según la documentación de Realmy
        payload_str = json.dumps(cuerpo, sort_keys=True, separators=(',', ':'))
        firma_esperada = hmac.new(webhook_secret.encode(), payload_str.encode(),
                                  hashlib.sha256).hexdigest()
        if firma_recibida != firma_esperada:
            return jsonify({"error": "Firma inválida."}), 403

    # Estado de la transacción según Realmy
    status_tx = cuerpo.get("x_transaction_status", "")
    referencia = cuerpo.get("x_ref_payco", "") or cuerpo.get("x_reference", "")

    # Extraer orden_id de la referencia (formato: RENTA-{orden_id})
    orden_id = None
    if referencia and referencia.startswith("RENTA-"):
        orden_id = referencia[6:].lower()

    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id) if orden_id else None
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404

    # Realmy estados: "Exitosa", "Fallida", "Pendiente", etc.
    if status_tx.lower() in ("exitosa", "succeeded", "aprobada", "approved"):
        orden["referencia_realmy"] = referencia
        orden["tx_id"] = cuerpo.get("x_transaction_id", "")
        # Unifica el cierre del pago: marca pagada, avisa al negocio, entrega al
        # cliente (plan PDF) y conserva la exógena/checklist (presentación).
        _finalizar_pago_orden(orden_id, orden, ordenes)
        _guardar_ordenes(ordenes)
        return jsonify({"status": "ok", "mensaje": "Pago confirmado."})

    elif status_tx.lower() in ("fallida", "failed", "rechazada", "rejected"):
        orden["estado"] = "pago_fallido"
        orden["razon_fallo"] = cuerpo.get("x_reason_text", "")
        _guardar_ordenes(ordenes)
        return jsonify({"status": "ok", "mensaje": "Pago rechazado — intente nuevamente."})

    else:
        # Pendiente u otro estado
        return jsonify({"status": "ok", "mensaje": "Transacción pendiente."})


# ------------------------------------------------------------ Wompi (Bancolombia)
def _orden_id_desde_referencia(ref: str) -> str:
    """Referencia 'RENTA-<orden_id>' → orden_id."""
    ref = ref or ""
    return ref[6:] if ref.startswith("RENTA-") else ref


@app.post("/api/checkout-wompi")
@login_requerido
def checkout_wompi():
    """Devuelve la URL de Web Checkout de Wompi para una orden."""
    if not wompi_mod.activo(WOMPI):
        return jsonify({"error": "Wompi no está habilitado."}), 400
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404

    monto_centavos = int(round(float(orden.get("precio", 0)))) * 100
    referencia = f"RENTA-{orden_id}"
    email = (orden.get("contacto") or {}).get("email", "")
    redirect_url = url_for("wompi_retorno", _external=True)
    url = wompi_mod.url_checkout(WOMPI, referencia, monto_centavos, redirect_url, email)

    orden["referencia_wompi"] = referencia
    _guardar_ordenes(ordenes)
    return jsonify({"url": url})


@app.get("/pago/wompi/retorno")
def wompi_retorno():
    """Wompi devuelve aquí al cliente tras pagar. Consultamos el estado real de
    la transacción y, si está aprobada, marcamos la orden como pagada."""
    tx_id = request.args.get("id", "")
    estado, orden_id = "desconocido", ""
    if tx_id and wompi_mod.activo(WOMPI):
        data = wompi_mod.consultar_transaccion(WOMPI, tx_id)
        status = (data.get("status") or "").upper()
        orden_id = _orden_id_desde_referencia(data.get("reference", ""))
        ordenes = _leer_ordenes()
        orden = ordenes.get(orden_id)
        if orden and orden.get("tipo") == "orden":
            if status == "APPROVED":
                if orden["estado"] in ("pendiente_pago", "pago_reportado", "pago_fallido"):
                    _finalizar_pago_orden(orden_id, orden, ordenes)
                    orden["tx_wompi"] = tx_id
                    _guardar_ordenes(ordenes)
                estado = "aprobado"
            elif status in ("DECLINED", "ERROR", "VOIDED"):
                orden["estado"] = "pago_fallido"
                orden["tx_wompi"] = tx_id
                _guardar_ordenes(ordenes)
                estado = "rechazado"
            else:
                estado = "pendiente"

    titulos = {
        "aprobado": ("✅ ¡Pago aprobado!", "Tu pago se procesó con éxito. Ya puedes descargar tu documento o continuar con tu trámite.", "#1e7d43"),
        "rechazado": ("❌ Pago rechazado", "El pago no se completó. Puedes intentarlo de nuevo desde la página.", "#c0392b"),
        "pendiente": ("⏳ Pago en proceso", "Tu pago está siendo verificado. Te avisaremos apenas se confirme.", "#e8a413"),
        "desconocido": ("Volviendo…", "No pudimos leer el resultado del pago. Si ya pagaste, escríbenos y lo verificamos.", "#5a6b7f"),
    }
    titulo, msg, color = titulos.get(estado, titulos["desconocido"])
    return render_template_string(_PAGINA_RETORNO, titulo=titulo, mensaje=msg, color=color)


@app.post("/api/wompi-webhook")
def wompi_webhook():
    """Webhook de eventos de Wompi (respaldo del retorno). Registra su URL en el
    panel de Wompi: https://TU-DOMINIO/api/wompi-webhook"""
    evento = request.get_json(silent=True) or {}
    if not wompi_mod.validar_firma_evento(WOMPI, evento):
        return jsonify({"error": "Firma inválida."}), 403
    tx = (evento.get("data", {}) or {}).get("transaction", {}) or {}
    status = (tx.get("status") or "").upper()
    orden_id = _orden_id_desde_referencia(tx.get("reference", ""))
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if orden and orden.get("tipo") == "orden" and status == "APPROVED":
        if orden["estado"] in ("pendiente_pago", "pago_reportado", "pago_fallido"):
            _finalizar_pago_orden(orden_id, orden, ordenes)
            orden["tx_wompi"] = tx.get("id", "")
            _guardar_ordenes(ordenes)
    return jsonify({"status": "ok"})


_PAGINA_RETORNO = """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Resultado del pago</title>
<style>body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#f5f8f7;margin:0;
min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.caja{background:#fff;border-radius:18px;max-width:440px;padding:38px 32px;text-align:center;
box-shadow:0 18px 50px rgba(10,25,45,.2)}h1{font-size:1.5rem;margin:0 0 12px;color:{{ color }}}
p{color:#5a6b7f;line-height:1.6}a{display:inline-block;margin-top:22px;background:#2e8f77;color:#fff;
text-decoration:none;padding:12px 22px;border-radius:12px;font-weight:700}</style></head>
<body><div class="caja"><h1>{{ titulo }}</h1><p>{{ mensaje }}</p>
<a href="/mi-cuenta">Ir a mi cuenta</a></div></body></html>"""


@app.get("/api/orden/<orden_id>/documentos.pdf")
@login_requerido
def descargar_checklist(orden_id):
    """Checklist de documentos soporte — para órdenes pagadas."""
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    if not str(orden.get("estado", "")).startswith("pagada"):
        return jsonify({"error": "La orden aún no registra pago."}), 402
    carga = ordenes.get(orden.get("token", ""), {})
    limite = fecha_limite(carga.get("nit", orden.get("nit", "")), PLANTILLA)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_checklist_pdf(salida, nombre=carga.get("nombre", orden.get("nombre", "")),
                              fecha_limite=str(limite) if limite else None)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)
    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name="Documentos_declaracion_renta.pdf",
                     mimetype="application/pdf")


@app.get("/api/orden/<orden_id>/guia-dian.pdf")
@login_requerido
def descargar_guia_dian(orden_id):
    """Guía paso a paso para que el cliente suba él mismo su Formulario 210
    a la DIAN. Acompaña al plan PDF (el borrador). Contenido genérico: no
    requiere que la orden esté pagada; se personaliza con nombre y fecha si
    la orden existe."""
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id) or {}
    carga = ordenes.get(orden.get("token", ""), {})
    limite = fecha_limite(carga.get("nit", orden.get("nit", "")), PLANTILLA)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_guia_dian_pdf(salida, nombre=carga.get("nombre", orden.get("nombre", "")),
                              fecha_limite=str(limite) if limite else None)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)
    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name="Guia_presentar_declaracion_DIAN.pdf",
                     mimetype="application/pdf")


# ======================================================================
# Cuenta de usuario: login social, cédula → vencimiento, recordatorios
# ======================================================================

def _fecha_texto(limite):
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{limite.day} de {meses[limite.month]} de {limite.year}" if limite else None


@app.get("/login")
def login_page():
    siguiente = request.args.get("next", "/mi-cuenta")
    if usuario_actual():
        return redirect(siguiente)
    return render_template("login.html", error=request.args.get("error"), next=siguiente)


@app.get("/mi-cuenta")
@login_requerido
def mi_cuenta():
    u = usuario_actual()
    limite = u.fecha_limite
    dias = (limite - date.today()).days if limite else None
    return render_template("mi_cuenta.html", u=u.to_dict(),
                           fecha_texto=_fecha_texto(limite), dias_restantes=dias)


@app.post("/api/mi-cuenta/cedula")
@login_requerido
def guardar_cedula():
    """Guarda la cédula/NIT del usuario y calcula su fecha de vencimiento."""
    cuerpo = request.get_json(silent=True) or {}
    cedula = "".join(c for c in str(cuerpo.get("cedula", "")) if c.isdigit())
    if len(cedula) < 2:
        return jsonify({"error": "Ingresa una cédula o NIT válido."}), 400

    u = usuario_actual()
    u.cedula = cedula
    limite = fecha_limite(cedula, PLANTILLA)
    u.fecha_limite = limite
    db.session.commit()

    dias = (limite - date.today()).days if limite else None
    return jsonify({
        "cedula": cedula,
        "nit_final": cedula[-2:],
        "fecha_limite": _fecha_texto(limite),
        "fecha_limite_iso": str(limite) if limite else None,
        "dias_restantes": dias,
    })


@app.post("/api/mi-cuenta/preferencias")
@login_requerido
def guardar_preferencias():
    """Actualiza recordatorios y solicitud de asesor."""
    cuerpo = request.get_json(silent=True) or {}
    u = usuario_actual()
    if "acepta_recordatorios" in cuerpo:
        u.acepta_recordatorios = bool(cuerpo["acepta_recordatorios"])

    # Cada activación de la casilla avisa al negocio: un lead que insiste vale
    # más que el riesgo de un correo repetido (desactivarla nunca notifica).
    pidio_asesor_ahora = False
    if "quiere_asesor" in cuerpo:
        nuevo = bool(cuerpo["quiere_asesor"])
        pidio_asesor_ahora = nuevo
        u.quiere_asesor = nuevo
    db.session.commit()

    aviso_enviado = False
    if pidio_asesor_ahora:
        from src.correo import notificar_solicitud_asesor
        aviso_enviado = notificar_solicitud_asesor(
            nombre=u.nombre or "", email_usuario=u.email or "",
            cedula=u.cedula or "", limite=u.fecha_limite)

    return jsonify({"acepta_recordatorios": u.acepta_recordatorios,
                    "quiere_asesor": u.quiere_asesor,
                    "aviso_enviado": aviso_enviado})


@app.get("/admin")
@autorizado_requerido
def admin():
    """Panel local para verificar consignaciones y gestionar trámites.
    OJO: sin autenticación — solo para uso local. Agregar login antes de
    publicar en internet."""
    ordenes = _leer_ordenes()
    filas = []
    for oid, o in sorted(ordenes.items(), key=lambda kv: kv[1].get("fecha", ""), reverse=True):
        if o.get("tipo") != "orden":
            continue
        c = o.get("contacto", {})
        estado = o.get("estado", "")
        color = {"pendiente_pago": "#b3372f", "pago_reportado": "#e8a413",
                 "pagada": "#1e7d43", "pagada_en_tramite": "#1e7d43"}.get(estado, "#555")
        acciones = ""
        if estado in ("pendiente_pago", "pago_reportado"):
            acciones = (f"<button onclick=\"confirmar('{oid}')\" "
                        f"style='background:#1e7d43;color:#fff;border:0;border-radius:6px;"
                        f"padding:6px 10px;cursor:pointer'>✓ Confirmar pago</button>")
            if o.get("plan") == "contadores":
                acciones += ("<br><small style='color:#1e7d43'>al confirmar se le "
                             "habilita el liquidador solo (con su correo)</small>")
        elif o.get("plan") == "contadores":
            correo_ok = ("📧 correo de acceso enviado" if o.get("bienvenida_pase_enviada")
                         else (f"<button onclick=\"confirmar('{oid}')\" "
                               f"style='background:#123f6b;color:#fff;border:0;border-radius:6px;"
                               f"padding:5px 9px;cursor:pointer'>📧 Enviar correo de acceso</button>"))
            acciones = (f"<small style='color:#1e7d43'>✓ acceso al liquidador habilitado</small>"
                        f"<br>{correo_ok}")
        else:
            acciones = (f"<a href='/api/orden/{oid}/formulario.pdf'>F210 PDF</a> · "
                        f"<a href='/api/orden/{oid}/documentos.pdf'>Checklist</a> · "
                        f"<a href='/api/orden/{oid}/exogena.xlsx'>Exógena</a>")
        filas.append(
            f"<tr><td>{o.get('fecha','')}</td><td><code>{oid}</code></td>"
            f"<td>{o.get('nombre','')}<br><small>{o.get('nit','')}</small></td>"
            f"<td>{('👔 ' + _CFG_PRECIOS.get('contadores',{}).get('nombre','Pase de temporada')) if o.get('plan')=='contadores' else PLANES.get(o.get('plan',''),{}).get('nombre', o.get('plan',''))}</td>"
            f"<td style='text-align:right'>${o.get('precio',0):,.0f}</td>"
            f"<td>{c.get('nombre','')}<br><small>{c.get('email','')} {c.get('telefono','')}</small></td>"
            f"<td style='color:{color};font-weight:700'>{estado.replace('_',' ')}</td>"
            f"<td>{acciones}</td></tr>")
    cuenta = f"{PAGO.get('banco','')} {PAGO.get('tipo','')} {PAGO.get('numero','')}"

    # ---- usuarios registrados (login social / demo) ----
    filas_u = []
    n_asesor = 0
    for u in Usuario.query.order_by(Usuario.ultimo_acceso.desc()).all():
        d = u.to_dict()
        limite = _fecha_texto(u.fecha_limite) or "—"
        dias = ""
        if u.fecha_limite:
            n = (u.fecha_limite - date.today()).days
            dias = f" <small>({n} días)</small>" if n >= 0 else f" <small style='color:#b3372f'>(venció)</small>"
        rec = "🔔 sí" if d["acepta_recordatorios"] else "🔕 no"
        if d["quiere_asesor"]:
            n_asesor += 1
            asesor = "<b style='color:#b3372f'>⚑ PIDIÓ ASESOR</b>"
            fila_bg = " style='background:#fff6f5'"
        else:
            asesor = "<span style='color:#9db0c4'>—</span>"
            fila_bg = ""
        prov = {"google": "Google", "microsoft": "Microsoft", "demo": "demo"}.get(u.proveedor, u.proveedor or "")
        cedula_txt = d['cedula'] or "<span style='color:#9db0c4'>sin cédula</span>"
        nombre_txt = d['nombre'] or ""
        email_txt = d['email'] or ""
        filas_u.append(
            f"<tr{fila_bg}><td>{nombre_txt}<br><small>{prov}</small></td>"
            f"<td>{email_txt}</td>"
            f"<td>{cedula_txt}</td>"
            f"<td>{limite}{dias}</td>"
            f"<td>{rec}</td>"
            f"<td>{asesor}</td></tr>")

    aviso_asesor = (f"<p style='background:#fff6f5;border:1px solid #f0c8c4;padding:10px 14px;"
                    f"border-radius:8px'>⚑ <b>{n_asesor}</b> usuario(s) solicitaron que un asesor "
                    f"los contacte.</p>" if n_asesor else "")

    # ---- contadores que usaron su muestra gratis (termómetro + reinicio) ----
    filas_m = []
    for m in MuestraContador.query.order_by(MuestraContador.creado.desc()).all():
        fecha_m = m.creado.strftime("%Y-%m-%d %H:%M") if m.creado else ""
        filas_m.append(
            f"<tr><td>{fecha_m}</td><td>{m.email or ''}</td>"
            f"<td>{m.nit_muestra or '—'}</td>"
            f"<td><button onclick=\"reiniciar({m.usuario_id})\" "
            f"style='background:#b3372f;color:#fff;border:0;border-radius:6px;"
            f"padding:6px 10px;cursor:pointer'>↺ Reiniciar prueba</button></td></tr>")

    # ---- contadores habilitados al liquidador (pase de temporada) ----
    filas_a = []
    for a in AccesoAutorizado.query.order_by(AccesoAutorizado.creado.desc()).all():
        fecha_a = a.creado.strftime("%Y-%m-%d") if a.creado else ""
        filas_a.append(
            f"<tr><td>{a.email}</td><td>{a.nombre or '—'}</td>"
            f"<td>{a.nota or ''}</td><td>{fecha_a}</td>"
            f"<td><button onclick=\"revocar('{a.email}')\" "
            f"style='background:#b3372f;color:#fff;border:0;border-radius:6px;"
            f"padding:6px 10px;cursor:pointer'>✕ Quitar acceso</button></td></tr>")

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Admin — Panel</title>
<style>body{{font-family:-apple-system,sans-serif;margin:24px;color:#1e2b3a}}
table{{border-collapse:collapse;width:100%;font-size:.85rem;margin-bottom:34px}}
th,td{{border-bottom:1px solid #dbe3ec;padding:8px;text-align:left;vertical-align:top}}
th{{background:#123f6b;color:#fff}}
h2{{margin-top:10px}}
button{{transition:transform .15s ease, box-shadow .15s ease; cursor:pointer}}
button:hover:not(:disabled){{transform:translateY(-2px); box-shadow:0 8px 18px rgba(10,25,45,.18)}}
</style></head><body>
<h2>👥 Usuarios registrados ({len(filas_u)})</h2>
<p>Personas que ingresaron con Google/Microsoft (o demo) y dejaron sus datos.</p>
{aviso_asesor}
<table><tr><th>Nombre</th><th>Correo</th><th>Cédula/NIT</th><th>Vencimiento</th>
<th>Recordatorios</th><th>Asesor</th></tr>{''.join(filas_u) or
'<tr><td colspan=6>Aún no hay usuarios registrados.</td></tr>'}</table>

<h2>💳 Órdenes — verificación de consignaciones</h2>
<p>Cuenta de recaudo: <b>{cuenta}</b>. Verifique en su app Bancolombia que la
consignación llegó (valor y referencia) antes de confirmar.</p>
<table><tr><th>Fecha</th><th>Orden</th><th>Cliente</th><th>Plan</th><th>Valor</th>
<th>Contacto</th><th>Estado</th><th>Acciones</th></tr>{''.join(filas) or
'<tr><td colspan=8>Sin órdenes todavía.</td></tr>'}</table>

<h2>👔 Contadores que probaron gratis ({len(filas_m)})</h2>
<p>Cada uno ya usó su declaración de muestra (termómetro de interés).
"Reiniciar" le devuelve su prueba gratis — útil para demos.</p>
<table><tr><th>Fecha</th><th>Contador</th><th>NIT muestra</th><th>Acción</th></tr>{''.join(filas_m) or
'<tr><td colspan=4>Ningún contador ha probado todavía.</td></tr>'}</table>

<h2>🔑 Acceso al liquidador — contadores con pase ({len(filas_a)})</h2>
<p>Habilita a un contador que pagó el pase de temporada. Usa el <b>mismo correo</b>
con el que él entra por Google o Microsoft. Le da acceso a <code>/liquidador</code>
(declaraciones ilimitadas), <b>NO</b> a este panel de pagos.</p>
<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-bottom:12px">
  <label style="font-size:.78rem">Correo del contador<br>
    <input id="acEmail" type="email" placeholder="contador@gmail.com"
      style="padding:8px;border:1px solid #dbe3ec;border-radius:6px;width:250px"></label>
  <label style="font-size:.78rem">Nombre (opcional)<br>
    <input id="acNombre" placeholder="Nombre del contador"
      style="padding:8px;border:1px solid #dbe3ec;border-radius:6px;width:200px"></label>
  <button onclick="otorgar()" style="background:#1e7d43;color:#fff;border:0;
    border-radius:6px;padding:10px 18px;cursor:pointer;font-weight:700">+ Dar acceso</button>
</div>
<table><tr><th>Correo</th><th>Nombre</th><th>Nota</th><th>Desde</th><th>Acción</th></tr>{''.join(filas_a) or
'<tr><td colspan=5>Ningún contador habilitado todavía.</td></tr>'}</table>
<script>
async function otorgar() {{
  const email = document.getElementById('acEmail').value.trim();
  const nombre = document.getElementById('acNombre').value.trim();
  if (!email) {{ alert('Escribe el correo del contador.'); return; }}
  const r = await fetch('/api/acceso/otorgar', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{email, nombre}})}});
  const j = await r.json();
  if (r.ok) location.reload(); else alert(j.error || 'Error');
}}
async function revocar(email) {{
  if (!confirm('¿Quitar el acceso al liquidador de ' + email + '?')) return;
  const r = await fetch('/api/acceso/revocar', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{email}})}});
  if (r.ok) location.reload(); else alert('Error');
}}
async function confirmar(oid) {{
  if (!confirm('¿Confirmar que la consignación de la orden ' + oid + ' llegó a la cuenta?')) return;
  const r = await fetch('/api/confirmar-pago', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{orden_id: oid}})}});
  if (r.ok) location.reload(); else alert('Error confirmando');
}}
async function reiniciar(uid) {{
  if (!confirm('¿Devolverle su prueba gratis a este contador?')) return;
  const r = await fetch('/api/muestra-contador/reset', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{usuario_id: uid}})}});
  if (r.ok) location.reload(); else alert('Error reiniciando');
}}
</script></body></html>"""


@app.post("/api/muestra-contador/reset")
@autorizado_requerido
def reset_muestra_contador():
    """Devuelve la prueba gratis a un contador (borra su registro de muestra).
    Solo personal autorizado, desde /admin."""
    cuerpo = request.get_json(silent=True) or {}
    uid = cuerpo.get("usuario_id")
    fila = db.session.get(MuestraContador, uid) if uid is not None else None
    if fila is None:
        return jsonify({"error": "No encontrado."}), 404
    db.session.delete(fila)
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/api/acceso/otorgar")
@autorizado_requerido
def otorgar_acceso():
    """Habilita a un contador (por correo) al liquidador profesional, sin editar
    el Secret File ni redesplegar. Solo personal autorizado, desde /admin.
    El correo debe ser el mismo con el que el contador entra por Google/Microsoft."""
    cuerpo = request.get_json(silent=True) or {}
    email = (cuerpo.get("email") or "").strip().lower()
    nombre = (cuerpo.get("nombre") or "").strip()
    nota = (cuerpo.get("nota") or "Pase de temporada").strip()
    if not email or "@" not in email:
        return jsonify({"error": "Correo inválido."}), 400
    if db.session.get(AccesoAutorizado, email) is not None:
        return jsonify({"error": "Ese correo ya tiene acceso."}), 409
    db.session.add(AccesoAutorizado(email=email, nombre=nombre, nota=nota))
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/api/acceso/revocar")
@autorizado_requerido
def revocar_acceso():
    """Quita el acceso al liquidador de un contador. Solo personal autorizado."""
    cuerpo = request.get_json(silent=True) or {}
    email = (cuerpo.get("email") or "").strip().lower()
    fila = db.session.get(AccesoAutorizado, email)
    if fila is None:
        return jsonify({"error": "No encontrado."}), 404
    db.session.delete(fila)
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/api/eliminar-datos")
@login_requerido
def eliminar_datos():
    """El cliente que no continúa puede borrar su archivo y sus datos.

    Elimina la exógena subida, el registro de la carga y la copia en memoria.
    Las exógenas de trámites de presentación ya pagados se conservan (el
    cliente aceptó el servicio y se necesitan para presentar la declaración).
    """
    cuerpo = request.get_json(silent=True) or {}
    token = cuerpo.get("token", "")
    ordenes = _leer_ordenes()
    carga = ordenes.get(token)
    if not carga or carga.get("tipo") != "carga":
        return jsonify({"error": "No hay datos para eliminar."}), 404

    Path(carga.get("archivo", "/nonexistent")).unlink(missing_ok=True)
    try:   # también la copia del Excel en la BD (la de "orden:<id>" pagada se conserva)
        ArchivoExogena.query.filter_by(id=token).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
    _EXOGENAS.pop(token, None)
    del ordenes[token]
    # órdenes no pagadas asociadas también se eliminan
    for oid in [k for k, o in ordenes.items()
                if o.get("tipo") == "orden" and o.get("token") == token
                and o.get("estado") == "pendiente_pago"]:
        del ordenes[oid]
    _guardar_ordenes(ordenes)
    return jsonify({"eliminado": True})


@app.get("/api/orden/<orden_id>/exogena.xlsx")
@autorizado_requerido
def descargar_exogena_orden(orden_id):
    """Excel de la exógena de un trámite (para el personal). Se lee de la BD;
    si no está, se intenta el archivo local como respaldo."""
    fila = _leer_archivo_bd(f"orden:{orden_id}")
    if fila is None:
        orden = _leer_ordenes().get(orden_id) or {}
        fila = _leer_archivo_bd(orden.get("token", ""))
        if fila is None:
            ruta = Path(orden.get("archivo_cliente", "/nonexistent"))
            if ruta.exists():
                return send_file(ruta, as_attachment=True, download_name=ruta.name)
            return jsonify({"error": "No hay Excel guardado para esta orden."}), 404
    return send_file(io.BytesIO(fila.datos), as_attachment=True,
                     download_name=fila.nombre or f"{orden_id}_exogena.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/orden/<orden_id>/formulario.pdf")
@login_requerido
def descargar_orden_pdf(orden_id):
    """Entrega el Formulario 210 en PDF solo si la orden está pagada."""
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    if not str(orden.get("estado", "")).startswith("pagada"):
        return jsonify({"error": "La orden aún no registra pago."}), 402
    carga = ordenes.get(orden.get("token", ""), {})
    try:
        datos = DatosDeclaracion.from_dict(carga.get("datos", {}))
    except (TypeError, KeyError):
        return jsonify({"error": "No hay datos asociados a la orden."}), 410
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_formulario_pdf(salida, datos, liq, PARAMS)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)
    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name=f"Formulario210_{orden.get('nit','')}.pdf",
                     mimetype="application/pdf")


@app.post("/api/formulario-pdf")
@pro_requerido
def formulario_pdf():
    """PDF con el layout del formulario 210 oficial (marcado BORRADOR)."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_formulario_pdf(salida, datos, liq, PARAMS)
        sellar_formulario_pdf(salida)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name=f"Formulario210_{nit}.pdf",
        mimetype="application/pdf",
    )


@app.post("/api/firmar-pdf")
@pro_requerido
def firmar_formulario_pdf():
    """Formulario 210 firmado con el certificado .p12/.pfx del usuario (PAdES).

    El certificado y su contraseña se procesan en memoria y no se guardan ni se
    registran en logs. La firma acredita integridad y origen del borrador; NO
    presenta la declaración ante la DIAN (eso ocurre solo en el portal MUISCA).
    """
    archivo = request.files.get("certificado")
    passphrase = request.form.get("passphrase", "")
    if archivo is None or not archivo.filename:
        return jsonify({"error": "Adjunte su certificado .p12 o .pfx."}), 400

    try:
        datos = DatosDeclaracion.from_dict(json.loads(request.form.get("datos", "{}")))
    except (TypeError, KeyError, ValueError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400

    certificado = archivo.read()
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        borrador = Path(tmp.name)
    firmado = borrador.with_name(f"{borrador.stem}_firmado.pdf")
    try:
        generar_formulario_pdf(borrador, datos, liq, PARAMS)
        sellar_formulario_pdf(borrador)          # el sello reescribe: va antes de firmar
        firmar_pdf(borrador, certificado, passphrase, razon=AVISO_LEGAL, salida=firmado)
        contenido = firmado.read_bytes()
    except FirmaError as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        del certificado, passphrase
        borrador.unlink(missing_ok=True)
        firmado.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name=f"Formulario210_{nit}_firmado.pdf",
        mimetype="application/pdf",
    )


@app.after_request
def sin_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://127.0.0.1:5210")
    app.run(host="127.0.0.1", port=5210, debug=False)
