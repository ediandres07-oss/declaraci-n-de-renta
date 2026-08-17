"""Aplicación web local: arrastre la exógena y genere el Formulario 210.

Reutiliza el mismo motor que la CLI (src/): parser de exógena, mapeo,
motor de cálculo y escritura del Excel. Todo se procesa localmente.

Ejecutar:  .venv/bin/python webapp.py   →  http://127.0.0.1:5210
"""
import io
import json
import os
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
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   render_template_string, request, send_file, session, url_for)

from src import whatsapp as wa_mod
from src import wompi as wompi_mod
from src.asistente import asistente_activo as asistente_ia_activo
from src.asistente import cargar_config as cargar_config_ia
from src.asistente import responder as responder_ia
from src.auth import (AccesoAutorizado, ArchivoExogena, LeadEspera, LeadExogena,
                      MuestraContador,
                      OrdenRegistro, Usuario, auth_bp, autorizado_requerido, db,
                      init_auth, login_requerido, pro_requerido, usuario_actual,
                      PLANES_LECTOR, SuscripcionLector, EmpresaLector,
                      crear_suscripcion, estado_licencia, registrar_empresa_lector,
                      generar_codigo_lector, entrar_con_codigo,
                      esta_bloqueado, limpiar_intentos_fallidos,
                      agente_consumir, agente_set)
from src.calendario import fecha_limite
from src.vencimientos import venc_bp, calendario_publico
from src.documentos import generar_checklist_pdf
from src.guia_dian import generar_guia_dian_pdf

from src.entrevista import mapear_exogena_a_datos
from src.excel_writer import escribir_borrador_comerciante, escribir_formulario
from src.exogena_parser import (ExogenaError, calcular_topes_propios,
                                evaluar_obligacion_declarar, parsear_exogena)
from src.modelos import DatosDeclaracion, ResultadoExogena
from src.motor_calculo import calcular
from src.parametros import Parametros
from src.firma import AVISO_LEGAL, FirmaError, firmar_pdf
from src.formulario_pdf import generar_formulario_pdf, sellar_formulario_pdf
from src.resumen_pdf import generar_resumen_pdf

BASE = Path(__file__).resolve().parent
PLANTILLA = BASE / "tests" / "fixtures" / "PapelesTrabajo-EnBlanco.xlsx"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB

# Autenticación social (Google/Microsoft) + BD de usuarios
_OAUTH_CFG = init_auth(app)
app.register_blueprint(auth_bp)

# Gestor de vencimientos para contadores (gratis con login)
app.register_blueprint(venc_bp)


# Pre-carga zoneinfo al arranque: si dos hilos lo importan a la vez por primera
# vez, uno ve el módulo "partially initialized" y el buscador IA da 502.
from zoneinfo import ZoneInfo  # noqa: E402


def _bucle_avisos_vencimientos():
    """Avisos diarios a contadores (7 y 3 días antes) sin cron externo: cada
    media hora mira si son las 8-9 a.m. de Bogotá y, con el candado en BD,
    un solo worker envía el lote del día."""
    from src.vencimientos import correr_avisos_diarios, VencimientoAviso
    from src.auth import db as _db
    from src import gerente as _ger

    def _candado(clave: str) -> bool:
        """True si ESTE worker ganó el turno de hoy para esa tarea."""
        try:
            _db.session.add(VencimientoAviso(usuario_id=0, clave=clave))
            _db.session.commit()
            return True
        except Exception:
            _db.session.rollback()
            return False

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
                if 7 <= ahora.hour < 9:
                    hoy = ahora.date().isoformat()
                    with app.app_context():
                        # Gerente virtual: informe diario del negocio.
                        if _candado(f"gerente|{hoy}"):
                            _ger.informe_diario()
                            print("[gerente] informe diario enviado")
                        # Onboarding del trial del Lector (secuencia días 1/7/20/28).
                        if _candado(f"onboarding|{hoy}"):
                            n = _ger.onboarding_lector()
                            if n:
                                print(f"[gerente] onboarding: {n} correo(s) enviados")
                        # Recuperación de leads del cálculo gratis de renta.
                        if _candado(f"recuperacion|{hoy}"):
                            n = _ger.recuperacion_leads()
                            if n:
                                print(f"[gerente] recuperación: {n} correo(s) enviados")
                        # Agente comercial: seguimientos del embudo de contadores
                        # (solo propone; el dueño aprueba cada correo).
                        if _candado(f"segcont|{hoy}"):
                            n = _ger.seguimiento_contadores()
                            if n:
                                print(f"[gerente] seguimiento contadores: {n} propuesto(s)")
                        # Lunes: lote de contenido de marketing.
                        if ahora.weekday() == 0 and _candado(f"mkt|{hoy}"):
                            _ger.contenido_semanal(IA_CFG)
                            print("[gerente] contenido semanal enviado")
        except Exception as exc:  # noqa: BLE001  — el hilo nunca debe morir
            print(f"[avisos-vencimientos] error: {exc}")
        time.sleep(1800)


threading.Thread(target=_bucle_avisos_vencimientos, daemon=True).start()


@app.before_request
def _capturar_origen():
    """Atribución: recuerda de dónde llegó el visitante (ads/instagram/…) en su
    primera visita, para sellarlo luego en leads, muestras y órdenes."""
    if request.method != "GET" or request.path.startswith(("/static", "/api/", "/admin")):
        return
    if request.args.get("bono"):
        session["bono"] = request.args.get("bono").strip().upper()[:20]
    if session.get("origen"):
        return
    a = request.args
    utm = (a.get("utm_source") or "").strip().lower()
    ref = (request.referrer or "").lower()
    org = ""
    if a.get("gclid") or utm in ("google", "google_ads", "ads", "adwords"):
        org = "ads"
    elif a.get("fbclid") or utm in ("ig", "instagram", "fb", "facebook", "meta"):
        org = "instagram"
    elif utm:
        org = utm[:30]
    elif "instagram" in ref or "facebook" in ref:
        org = "instagram"
    elif "youtube" in ref or "youtu.be" in ref:
        org = "youtube"
    elif "whatsapp" in ref or "wa.me" in ref:
        org = "whatsapp"
    elif "google" in ref:
        org = "google_organico"
    elif request.path.rstrip("/") == "/links":
        # /links solo se difunde en la bio de Instagram; el navegador de la app
        # no siempre manda referrer, así que la ruta misma es la marca.
        org = "instagram"
    if org:
        session["origen"] = org


def _origen_actual() -> str:
    """El canal de llegada guardado en la sesión ('' si entró directo)."""
    return session.get("origen", "")


def _canjear_bono(plan: str) -> tuple[str, int]:
    """(codigo, descuento) si hay un bono válido para ese plan.

    Cada plan usa SU tipo de bono ('pase' para contadores, 'renta' para los de
    la declaración). Se busca primero el bono de la sesión y, si no aplica (la
    sesión solo guarda uno y puede ser del otro tipo), un bono vigente del
    usuario logueado por su correo. No lo marca como usado (eso pasa al pago)."""
    from src.auth import Bono
    from src.gerente import BONO_PASE, BONO_RENTA
    tipo_nec = "pase" if plan == "contadores" else "renta"
    monto = BONO_PASE if tipo_nec == "pase" else BONO_RENTA.get(plan, 0)
    if not monto:
        return "", 0

    def _vigente(b):
        return (b is not None and not b.usado
                and not (b.expira and b.expira < datetime.utcnow()))

    cod = (session.get("bono") or "").strip().upper()
    if cod:
        b = db.session.get(Bono, cod)
        if _vigente(b) and b.tipo == tipo_nec:
            return cod, monto
    u = usuario_actual()
    if u is not None and u.email:
        b = (Bono.query.filter_by(email=u.email, tipo=tipo_nec, usado=False)
             .filter(Bono.expira > datetime.utcnow()).first())
        if _vigente(b):
            return b.codigo, monto
    return "", 0


def _sellar_origen_muestra(email: str) -> None:
    """Sella el origen (sesión, o el del código pedido antes) en la fila de la
    muestra ya registrada, sin pisar un valor existente."""
    try:
        from src.auth import CodigoMuestra, MuestraContadorEmail
        email = (email or "").strip().lower()
        fila = db.session.get(MuestraContadorEmail, email)
        if fila is None or fila.origen:
            return
        org = _origen_actual()
        if not org:
            cod = db.session.get(CodigoMuestra, email)
            org = (cod.origen if cod else "") or ""
        if org:
            fila.origen = org
            db.session.commit()
    except Exception:
        db.session.rollback()


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

# El Secret File de Render se monta en /etc/secrets/epayco.yaml; en local está
# en config/epayco.yaml. Se busca en ambos.
EPAYCO = {"habilitado": False}
for _ep in (BASE / "config" / "epayco.yaml", Path("/etc/secrets/epayco.yaml")):
    if _ep.exists():
        with open(_ep, "r", encoding="utf-8") as _fh:
            EPAYCO = yaml.safe_load(_fh) or EPAYCO
        break

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


def _exogena_de(token: str):
    """Exógena del token: de memoria o re-parseada desde la BD (sobrevive a
    los redeploys de Render, que borran _EXOGENAS)."""
    ex = _EXOGENAS.get(token or "")
    if ex is not None or not token:
        return ex
    fila = _leer_archivo_bd(token)
    if fila is None:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(fila.datos)
            ruta = Path(tmp.name)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ex = parsear_exogena(ruta)
        ruta.unlink(missing_ok=True)
        _EXOGENAS[token] = ex
        return ex
    except Exception:
        return None


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
    # Bono de $30.000 para todo usuario con sesión: se crea (o renueva) solo, y
    # la tarjeta/modal muestran el precio tachado con el descuento aplicado.
    u = usuario_actual()
    if u is not None and u.email:
        try:
            if not _canjear_bono("presentacion")[1]:
                from src.auth import Bono
                from src.gerente import _crear_bono
                bx = (Bono.query.filter_by(email=u.email, tipo="renta", usado=False)
                      .filter(Bono.expira > datetime.utcnow()).first())
                if bx is None:
                    bx = db.session.get(Bono, _crear_bono(u.email, "renta"))
                session["bono"] = bx.codigo
        except Exception:
            app.logger.warning("landing: no se pudo crear el bono del usuario")
    return render_template("landing.html", anio=PARAMS.anio_gravable,
                           planes=PLANES,
                           bono_pdf=_canjear_bono("pdf")[1],
                           bono_presentacion=_canjear_bono("presentacion")[1],
                           realmy_habilitado=REALMY.get("habilitado"),
                           realmy_public_key=REALMY.get("public_key", ""),
                           realmy_merchant_id=REALMY.get("merchant_id", ""),
                           realmy_test=REALMY.get("test", True),
                           ia_habilitado=asistente_ia_activo(IA_CFG),
                           ia_nombre=IA_CFG.get("nombre_asistente", "Asistente"),
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""),
                           ia_correo=IA_CFG.get("negocio", {}).get("correo", ""),
                           wompi_habilitado=wompi_mod.activo(WOMPI),
                           usuario_logueado=usuario_actual() is not None,
                           usuario_nombre=getattr(usuario_actual(), "nombre", "") or "",
                           usuario_email=getattr(usuario_actual(), "email", "") or "")


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

    contexto = (cuerpo.get("contexto") or "").strip().lower()

    # Modo CONTADOR: atiende sobre el pase de cortesía y el Lector, con los
    # precios vigentes inyectados. No usa la liquidación del cliente.
    if contexto == "contador":
        try:
            respuesta = responder_ia(mensajes, IA_CFG, contexto="contador",
                                     system_extra=_info_contador_ia())
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                return jsonify({"error": "Estoy atendiendo muchas consultas en este momento. "
                                         "Espera unos segundos e inténtalo de nuevo. 🙏"}), 429
            app.logger.warning("Fallo del asistente (contador): %s", e)
            return jsonify({"error": "No pude responder ahora mismo. Intenta de nuevo en un momento."}), 502
        return jsonify({"respuesta": respuesta})

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
def _wa_avisar_traspaso(remitente: str, texto: str) -> None:
    """Avisa al dueño (Telegram + correo) que un contador pide hablar en persona."""
    seguro = (texto or "")[:200].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    aviso = (f"📲 Un contador pide hablar contigo en WhatsApp.\n"
             f"De: +{remitente}\n"
             f"Mensaje: \"{seguro}\"\n"
             f"Abre el chat: https://wa.me/{remitente}\n"
             f"(El bot se pausó 6h en ese chat para que le respondas tú.)")
    try:
        from src import gerente as _ger
        _ger._telegram_enviar(aviso)
    except Exception:
        pass
    try:
        from src.correo import enviar_email
        enviar_email("ediandres07@gmail.com",
                     "📲 Un contador quiere hablar contigo (WhatsApp)",
                     f"<pre style='font-family:-apple-system,sans-serif;font-size:14px;white-space:pre-wrap'>{aviso}</pre>")
    except Exception:
        pass


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
        # Los contadores llegan por WhatsApp (campañas + /contadores): el asistente
        # los atiende con el contexto de contador (pase de temporada / Lector XML).
        # Si piden hablar con una persona, el bot avisa al dueño y se calla en ese chat.
        wa_mod.atender(IA_CFG, payload,
                       lambda hist: responder_ia(hist, IA_CFG, contexto="contador"),
                       on_handoff=_wa_avisar_traspaso)
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


@app.get("/calendario-tributario-2026")
@app.get("/calendario")
def calendario_2026_publico():
    """Calendario tributario 2026 PÚBLICO e indexable (SEO). Muestra el rango de
    fechas de cada obligación; la fecha exacta por dígito de NIT y lo personalizado
    (avisos, PDF con logo, multi-cliente) queda tras el login en /vencimientos."""
    _M = ["", "ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]
    fmt = lambda d: f"{d.day} {_M[d.month]}"
    obligaciones = []
    for ob in calendario_publico(2026):
        periodos = [{
            "etiqueta": p["etiqueta"],
            "rango": fmt(p["hasta"]) if p["desde"] == p["hasta"]
                     else f"{fmt(p['desde'])} – {fmt(p['hasta'])}",
            "por_digito": p["por_digito"],
        } for p in ob["periodos"]]
        obligaciones.append({"nombre": ob["nombre"], "periodos": periodos})
    return render_template("calendario_publico.html", ano=2026, obligaciones=obligaciones)


# --- SEO: robots + sitemap (para que Google indexe las páginas públicas) ---
_URLS_PUBLICAS = ["/", "/calendario-tributario-2026", "/contabilidad",
                  "/contadores", "/links", "/descargar-lector", "/guia-dian",
                  "/lector-xml-dian", "/plano-contable-siigo",
                  "/plano-contable-contai", "/plano-contable-world-office",
                  "/plano-contable-helisa", "/iva-descontable-xml-dian",
                  "/descargar-xml-dian"]


@app.get("/robots.txt")
def robots_txt():
    cuerpo = ("User-agent: *\nAllow: /\n"
              "Sitemap: https://tributando.co/sitemap.xml\n")
    return Response(cuerpo, mimetype="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml():
    base = "https://tributando.co"
    urls = "".join(f"<url><loc>{base}{u}</loc></url>" for u in _URLS_PUBLICAS)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return Response(xml, mimetype="application/xml")


@app.get("/privacidad")
def privacidad():
    """Política de tratamiento de datos personales (Ley 1581 de 2012)."""
    return render_template("privacidad.html")


@app.get("/links")
@app.get("/enlaces")
def enlaces():
    """Página 'link en la bio': reúne todos los servicios en botones para redes."""
    return render_template("enlaces.html",
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""))


@app.get("/admin/dashboard")
@autorizado_requerido
def admin_dashboard():
    """Panel con el pulso del negocio: funnel B2C, pases de temporada y Lector."""
    from src import gerente as _ger
    return render_template("admin_dashboard.html", m=_ger.metricas_negocio(),
                           cont=_ger.metricas_contactos())


@app.get("/admin/lector")
@autorizado_requerido
def admin_lector():
    """Panel de suscripciones del Lector XML: quién vence, para controlar renovaciones."""
    subs = SuscripcionLector.query.order_by(SuscripcionLector.vence).all()
    hoy = date.today()
    filas = []
    for s in subs:
        dias = (s.vence - hoy).days if s.vence else None
        if not s.activa:
            estado, color = "Inactiva", "#8a919c"
        elif dias is not None and dias < 0:
            estado, color = f"Vencida ({-dias}d)", "#b91c1c"
        elif dias is not None and dias <= 7:
            estado, color = f"Vence en {dias}d", "#c47f0a"
        else:
            estado, color = f"Activa ({dias}d)", "#1f8a5f"
        empresas = EmpresaLector.query.filter_by(licencia=s.licencia).count()
        filas.append({"email": s.email, "plan": s.plan, "vence": s.vence,
                      "estado": estado, "color": color, "empresas": empresas,
                      "equipo": "sí" if s.equipo else "—", "licencia": s.licencia,
                      "agente": bool(s.agente)})
    html = """<!doctype html><html><head><meta charset="utf-8">
    <title>Suscripciones Lector</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f7fa;margin:0;padding:24px;color:#1e2432}
    h1{font-size:1.3rem}table{border-collapse:collapse;width:100%;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.05)}
    th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #eee;font-size:.9rem}th{background:#1e2432;color:#fff}
    .est{font-weight:700}a{color:#b8955f}
    .nav-admin{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 20px}
    .nav-admin a{display:inline-flex;align-items:center;gap:6px;background:#1e2432;color:#fff;
      text-decoration:none;padding:9px 16px;border-radius:8px;font-size:.9rem;font-weight:600}
    .nav-admin a.actual{background:#e8eef5;color:#1e2432;cursor:default;pointer-events:none}</style></head><body>
    <h1>🔑 Suscripciones — Lector XML ({{filas|length}})</h1>
    <div class="nav-admin">
      <a href="/admin">🏠 Órdenes y usuarios</a>
      <a class="actual">🔑 Suscripciones Lector XML</a>
      <a href="/vencimientos">📅 Gestor de vencimientos</a>
    </div>
    <div style="background:#fff;border-radius:12px;box-shadow:0 2px 10px rgba(0,0,0,.05);padding:14px 16px;margin-bottom:18px">
      <b style="font-size:1rem">🎁 Regalar / activar licencia (gratis)</b>
      <div style="color:#8a919c;font-size:.82rem;margin:4px 0 10px">Crea o renueva la licencia de un contador sin pago. Luego él entra con su <b>correo + código</b> (no necesita clave).</div>
      <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end">
        <div><label style="font-size:.75rem;display:block;color:#5b6472">Correo del contador</label><input id="czEmail" type="email" placeholder="correo@ejemplo.com" style="padding:8px;border:1px solid #d7dbe2;border-radius:8px;min-width:220px"></div>
        <div><label style="font-size:.75rem;display:block;color:#5b6472">Plan</label>
          <select id="czPlan" style="padding:8px;border:1px solid #d7dbe2;border-radius:8px">
            <option value="independiente_anual">Independiente (10 empresas)</option>
            <option value="pro_anual">Pro (30 empresas)</option>
            <option value="max_anual">Max (ilimitado)</option>
          </select></div>
        <div><label style="font-size:.75rem;display:block;color:#5b6472">Días</label><input id="czDias" type="number" value="365" style="padding:8px;border:1px solid #d7dbe2;border-radius:8px;width:90px"></div>
        <label style="font-size:.82rem;display:flex;align-items:center;gap:5px;color:#1e2432"><input id="czAgente" type="checkbox"> con Agente 🤖</label>
        <button onclick="cortesia()" style="background:#1f8a5f;color:#fff;border:0;padding:9px 16px;border-radius:8px;font-weight:600;cursor:pointer">Crear/activar gratis</button>
      </div>
      <div id="czMsg" style="font-size:.85rem;margin-top:8px"></div>
    </div>
    <table><tr><th>Correo</th><th>Plan</th><th>Vence</th><th>Estado</th><th>Empresas</th><th>Equipo</th><th>Agente</th><th></th></tr>
    {% for f in filas %}<tr><td>{{f.email}}</td><td>{{f.plan}}</td><td>{{f.vence or '—'}}</td>
    <td class="est" style="color:{{f.color}}">{{f.estado}}</td><td>{{f.empresas}}</td><td>{{f.equipo}}</td>
    <td><button onclick="agente('{{f.licencia}}',{{ 'false' if f.agente else 'true' }})" title="Activar/desactivar el complemento Asistente IA (agente)" style="border:0;background:none;cursor:pointer;font-size:1rem">{{ '🤖✅' if f.agente else '➕' }}</button></td>
    <td style="white-space:nowrap">
    {% if f.equipo == 'sí' %}<button onclick="liberar('{{f.licencia}}','{{f.email}}')" title="Liberar el equipo amarrado para que el contador active en otra máquina" style="border:0;background:none;cursor:pointer;font-size:1rem">🔓</button>{% endif %}
    <button onclick="borrar('{{f.licencia}}')" title="Borrar suscripción" style="border:0;background:none;cursor:pointer;color:#b91c1c">🗑</button></td></tr>{% endfor %}
    {% if not filas %}<tr><td colspan="8" style="color:#8a919c">Aún no hay suscripciones.</td></tr>{% endif %}
    </table>
    <p style="color:#8a919c;font-size:.82rem;margin-top:10px">🔓 Liberar equipo = desamarra la licencia de su máquina actual; el contador la puede reactivar en otra (se re-amarra sola en la próxima activación).<br>🤖 Agente = complemento de pago (Asistente IA que ejecuta 350/300, revisa clientes, calcula). Actívalo a quien pague el add-on; tope 150 acciones/mes por contador.</p>
    <script>async function borrar(lic){ if(!confirm('¿Borrar esta suscripción de prueba?'))return;
      await fetch('/admin/lector/borrar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({licencia:lic})});
      location.reload();}
    async function liberar(lic,email){ if(!confirm('¿Liberar el equipo de '+email+'?\\nQuedará libre para activarse en otra máquina.'))return;
      const r=await fetch('/admin/lector/liberar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({licencia:lic})});
      const d=await r.json(); if(!d.ok) alert(d.error||'No se pudo liberar.');
      location.reload();}
    async function agente(lic,on){ if(!confirm(on?'¿Activar el Agente (add-on) para esta licencia?':'¿Desactivar el Agente para esta licencia?'))return;
      const r=await fetch('/admin/lector/agente',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({licencia:lic,activo:on})});
      const d=await r.json(); if(!d.ok) alert(d.error||'No se pudo.');
      location.reload();}
    async function cortesia(){
      const email=document.getElementById('czEmail').value.trim();
      const plan=document.getElementById('czPlan').value, dias=document.getElementById('czDias').value||365;
      const agente=document.getElementById('czAgente').checked;
      const msg=document.getElementById('czMsg');
      if(!email||!email.includes('@')){ msg.style.color='#b91c1c'; msg.textContent='Escribe un correo válido.'; return; }
      const r=await fetch('/admin/lector/cortesia',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,plan,dias,agente})});
      const d=await r.json();
      if(!d.ok){ msg.style.color='#b91c1c'; msg.textContent=d.error||'No se pudo.'; return; }
      msg.style.color='#1f8a5f'; msg.textContent='✓ Licencia activa para '+d.email+' (vence '+d.vence+'). Que entre al Lector con su correo + código.';
      setTimeout(()=>location.reload(),1800);
    }</script></body></html>"""
    return render_template_string(html, filas=filas)


@app.post("/admin/lector/borrar")
@autorizado_requerido
def admin_lector_borrar():
    lic = (request.get_json(silent=True) or {}).get("licencia", "")
    sus = SuscripcionLector.query.filter_by(licencia=lic).first()
    if sus:
        EmpresaLector.query.filter_by(licencia=lic).delete()
        db.session.delete(sus)
        db.session.commit()
    return jsonify({"ok": True})


def _emails_de(b):
    """Lista de correos a la medida desde el cuerpo (texto o lista). None si no hay."""
    crudo = b.get("emails")
    if crudo is None:
        return None
    if isinstance(crudo, str):
        crudo = re.split(r"[\s,;]+", crudo)
    return [e for e in (crudo or []) if e and "@" in e]


@app.get("/admin/campana")
@autorizado_requerido
def admin_campana():
    """Panel de campañas: redactar, elegir público, preview, enviar e historial."""
    from src import gerente as _ger
    return render_template("admin_campana.html", historial=_ger.historial_campanas(),
                           cont=_ger.metricas_contactos())


@app.get("/admin/telegram/probar")
@autorizado_requerido
def admin_telegram_probar():
    """Manda un mensaje de prueba a Telegram para verificar la configuración."""
    from src import gerente as _ger
    if not _ger.telegram_configurado():
        return jsonify({"ok": False,
                        "error": "Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID en Render."}), 400
    ok = _ger._telegram_enviar(
        "✅ <b>Telegram conectado</b> a Tributando. Aquí te avisaré de cada venta. 💰")
    return jsonify({"ok": ok} if ok else
                   {"ok": False, "error": "No se pudo enviar. Revisa el token y el chat_id."})


@app.route("/admin/campana/preview", methods=["GET", "POST"])
@autorizado_requerido
def admin_campana_preview():
    """Cuántos destinatarios tendría la campaña (no envía). Acepta publico o una
    lista a la medida en 'emails'."""
    from src import gerente as _ger
    b = request.get_json(silent=True) or {}
    publico = b.get("publico") or request.args.get("publico", "personas")
    dest = _ger.destinatarios_campana(publico, emails=_emails_de(b))
    return jsonify({"ok": True, "publico": publico, "total": len(dest), "muestra": dest[:6]})


@app.post("/admin/campana/enviar")
@autorizado_requerido
def admin_campana_enviar():
    """Envía la campaña. Requiere {asunto, mensaje, confirmar:true}. Envuelve el
    mensaje en la plantilla de marca, registra el envío en el historial y nunca
    dispara sin confirmación explícita."""
    b = request.get_json(silent=True) or {}
    if not b.get("confirmar"):
        return jsonify({"ok": False, "error": "Falta confirmar:true"}), 400
    from src import gerente as _ger
    publico = b.get("publico", "personas")
    dest = _ger.destinatarios_campana(publico, emails=_emails_de(b))
    asunto = (b.get("asunto") or "").strip()
    mensaje = (b.get("mensaje") or b.get("html") or "").strip()
    if not asunto or not mensaje:
        return jsonify({"ok": False, "error": "Falta el asunto o el mensaje."}), 400
    if not dest:
        return jsonify({"ok": False, "error": "No hay destinatarios para ese público."}), 400
    html = mensaje if b.get("html") else _ger.envolver_campana(
        mensaje, (b.get("cta_txt") or "").strip(), (b.get("cta_url") or "https://tributando.co").strip())
    res = _ger.enviar_campana(asunto, html, dest)
    try:
        _ger.registrar_campana(asunto, publico if _emails_de(b) is None else "lista",
                               len(dest), res["enviados"], len(res["fallidos"]), dest)
    except Exception:
        app.logger.warning("campana: no se pudo registrar en el historial")
    return jsonify({"ok": True, "total": len(dest),
                    "enviados": res["enviados"], "fallidos": len(res["fallidos"])})


@app.post("/admin/gerente/probar")
@autorizado_requerido
def admin_gerente_probar():
    """Dispara a demanda el informe diario y/o el contenido semanal (para probar).
    Corre en segundo plano para no chocar con el timeout del worker (Gemini tarda)."""
    from src import gerente as _ger
    b = request.get_json(silent=True) or {}

    def _correr():
        with app.app_context():
            try:
                if b.get("informe", True):
                    _ger.informe_diario()
                if b.get("contenido"):
                    _ger.contenido_semanal(IA_CFG)
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("gerente/probar falló: %s", exc)

    threading.Thread(target=_correr, daemon=True).start()
    return jsonify({"ok": True, "mensaje": "Generando… el correo llega en ~1 minuto."})


@app.post("/admin/lector/cortesia")
@autorizado_requerido
def admin_lector_cortesia():
    """Crea/renueva una licencia GRATIS (cortesía) para el correo indicado."""
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if "@" not in email:
        return jsonify({"ok": False, "error": "Correo inválido."})
    plan = (b.get("plan") or "independiente_anual").lower()
    try:
        dias = int(b.get("dias") or 365)
    except (TypeError, ValueError):
        dias = 365
    sus = crear_suscripcion(email, plan, dias)
    if b.get("agente"):
        agente_set(sus.licencia, True)
    return jsonify({"ok": True, "email": email, "licencia": sus.licencia,
                    "vence": sus.vence.isoformat() if sus.vence else None})


@app.get("/admin/gestor")
@autorizado_requerido
def admin_gestor():
    """Quién está usando el Gestor de Vencimientos: contadores con clientes
    cargados, cuántos, y desde cuándo."""
    from src.vencimientos import ClienteContador
    from src.gerente import _es_propio
    filas = {}
    for c in ClienteContador.query.all():
        f = filas.setdefault(c.usuario_id, {"n": 0, "ultimo": None})
        f["n"] += 1
        if c.creado and (f["ultimo"] is None or c.creado > f["ultimo"]):
            f["ultimo"] = c.creado
    usuarios = {u.id: u for u in Usuario.query.filter(Usuario.id.in_(filas)).all()} if filas else {}
    cuerpo = []
    for uid, f in sorted(filas.items(), key=lambda kv: -(kv[1]["n"])):
        u = usuarios.get(uid)
        email = (u.email if u else f"usuario #{uid}") or ""
        propio = " <span style='color:#8a919c'>(tuyo)</span>" if _es_propio(email) else ""
        ult = f["ultimo"].strftime("%Y-%m-%d") if f["ultimo"] else "—"
        cuerpo.append(f"<tr><td>{(u.nombre if u else '') or '—'}</td><td>{email}{propio}</td>"
                      f"<td style='text-align:center'>{f['n']}</td><td>{ult}</td></tr>")
    if not cuerpo:
        cuerpo = ["<tr><td colspan=4 style='color:#8a919c'>Nadie ha cargado clientes todavía.</td></tr>"]
    return (f"<div style='font-family:sans-serif;max-width:720px;margin:30px auto;color:#1e2432'>"
            f"<h2>📅 Uso del Gestor de Vencimientos ({len(filas)} contador(es))</h2>"
            f"<p style='color:#5a6272'>Contadores con clientes cargados en /vencimientos. "
            f"<a href='/admin'>🏠 Órdenes</a> · <a href='/admin/dashboard'>📊 Panel del negocio</a> · "
            f"<a href='/admin/exogenas'>🧾 Leads del cálculo</a></p>"
            f"<table cellpadding=8 style='border-collapse:collapse;width:100%'>"
            f"<tr style='text-align:left;background:#f3ede1'><th>Contador</th><th>Correo</th>"
            f"<th>Clientes</th><th>Último cargado</th></tr>{''.join(cuerpo)}</table></div>")


@app.get("/admin/exogenas")
@autorizado_requerido
def admin_exogenas():
    """Leads reales del cálculo gratis: solo cargas CON correo, sin las del
    dueño ni cuentas propias. Las viejas sin correo quedan como conteo."""
    from src.auth import LeadExogena
    from src.gerente import _es_propio
    leads = {(l.token or ""): l for l in LeadExogena.query.all()}
    compradores = set()
    cargas, sin_correo = [], 0
    ordenes = _leer_ordenes()
    for oid, d in ordenes.items():
        if d.get("tipo") == "orden" and d.get("estado") == "pagada":
            e = ((d.get("contacto") or {}).get("email") or "").lower()
            if e:
                compradores.add(e)
    for oid, d in ordenes.items():
        if d.get("tipo") != "carga":
            continue
        if "".join(ch for ch in str(d.get("nit") or "") if ch.isdigit()) == "3362058":
            continue                                   # cargas del dueño
        lead = leads.get(oid)
        correo = (lead.email if lead else "") or ""
        if not correo or _es_propio(correo):
            sin_correo += 1
            continue
        cargas.append({
            "fecha": d.get("fecha_carga", ""), "nombre": d.get("nombre", ""),
            "nit": d.get("nit", ""), "correo": correo,
            "compro": correo in compradores,
        })
    cargas.sort(key=lambda c: c["fecha"], reverse=True)
    filas = "".join(
        f"<tr><td>{c['fecha']}</td><td>{c['nombre'] or '—'}</td><td>{c['nit'] or '—'}</td>"
        f"<td>{c['correo']}</td>"
        f"<td>{'✅' if c['compro'] else '—'}</td></tr>" for c in cargas)
    if not filas:
        filas = "<tr><td colspan=5 style='color:#8a919c'>Aún no hay leads con correo — desde hoy el resultado pide el correo antes de mostrarse, aquí irán apareciendo.</td></tr>"
    return (f"<div style='font-family:sans-serif;max-width:760px;margin:30px auto;color:#1e2432'>"
            f"<h2>🧾 Leads del cálculo de renta ({len(cargas)})</h2>"
            f"<p style='color:#5a6272'>Solo se listan los que dejaron correo (sin tus cargas de prueba). "
            f"Subidas históricas sin correo: {sin_correo}. "
            f"<a href='/admin'>🏠 Órdenes</a> · <a href='/admin/dashboard'>📊 Panel del negocio</a> · "
            f"<a href='/admin/gestor'>📅 Uso del Gestor</a></p>"
            f"<table border=0 cellpadding=8 style='border-collapse:collapse;width:100%'>"
            f"<tr style='text-align:left;background:#f3ede1'><th>Fecha</th><th>Nombre</th>"
            f"<th>NIT/Cédula</th><th>Correo (lead)</th><th>Compró</th></tr>{filas}</table></div>")


@app.get("/admin/seguimiento/aprobar")
@autorizado_requerido
def admin_seguimiento_aprobar():
    """Botón 'Aprobar y enviar' del correo resumen del agente comercial."""
    from src import gerente as _ger
    r = _ger.seguimiento_aprobar(request.args.get("email", ""), request.args.get("paso", ""))
    color, msg = ("#1b7f4b", f"✅ Seguimiento enviado a {r.get('email')}") if r.get("ok") \
        else ("#b3372f", f"⚠️ {r.get('error')}")
    return (f"<div style='font-family:sans-serif;max-width:480px;margin:60px auto;"
            f"text-align:center;color:{color};font-size:18px'>{msg}"
            f"<br><br><a href='/admin' style='color:#8a6d3b'>Volver al panel</a></div>")


@app.post("/admin/lector/plan")
@autorizado_requerido
def admin_lector_plan():
    """Cambia el plan de una suscripción del Lector (sin tocar el vencimiento)."""
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    plan = (b.get("plan") or "").strip()
    from src.auth import PLANES_LECTOR as _PL
    if plan not in _PL:
        return jsonify({"error": f"Plan desconocido: {plan}"}), 400
    sus = SuscripcionLector.query.filter(
        db.func.lower(SuscripcionLector.email) == email).first()
    if sus is None:
        return jsonify({"error": "No hay suscripción con ese correo."}), 404
    anterior = sus.plan
    sus.plan = plan
    db.session.commit()
    return jsonify({"ok": True, "email": email, "antes": anterior, "ahora": plan,
                    "empresas_max": _PL[plan]["empresas_max"]})


@app.post("/admin/lector/agente")
@autorizado_requerido
def admin_lector_agente():
    """Activa/desactiva el complemento Agente (add-on) de una licencia."""
    b = request.get_json(silent=True) or {}
    return jsonify(agente_set(b.get("licencia", ""), bool(b.get("activo"))))


@app.post("/admin/lector/liberar")
@autorizado_requerido
def admin_lector_liberar():
    """Desamarra la licencia de su equipo actual: la próxima activación (en la
    máquina que sea) la vuelve a amarrar. Para trasladar sin tocar la base."""
    lic = (request.get_json(silent=True) or {}).get("licencia", "")
    sus = SuscripcionLector.query.filter_by(licencia=lic).first()
    if not sus:
        return jsonify({"ok": False, "error": "Licencia no encontrada."}), 404
    sus.equipo = None
    db.session.commit()
    return jsonify({"ok": True})


@app.get("/contadores/lector")
def contadores_lector():
    """Landing del Lector XML (Tributando Contadores): planes por empresa + pago.
    Al confirmar el pago se crea la suscripción y se entrega la clave de licencia."""
    u = usuario_actual()
    planes = []
    promo = _promo_lector_activa()
    for t in TIERS_LECTOR:
        c = t["clave"]
        pm, pa = f"{c}_mensual", f"{c}_anual"
        planes.append({
            "clave": c, "nombre": t["nombre"], "empresas_max": t["empresas_max"],
            "empresas": "Empresas ilimitadas" if not t["empresas_max"] else f"Hasta {t['empresas_max']} empresas",
            "mensual": precio_lector(pm),
            "anual": precio_lector(pa),
            "anual_normal": PRECIOS_LECTOR[pa],
            "promo_anual": promo and pa in PROMO_LECTOR["precios"],
            "agente_mensual": plan_incluye_agente(pm),
            "agente_anual": plan_incluye_agente(pa),
            "destacado": c == "pro",
        })
    _meses_es = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                 "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    _v = PROMO_LECTOR["vence"]
    return render_template("contadores_lector.html",
                           planes=planes,
                           promo_activa=promo,
                           promo_etiqueta=PROMO_LECTOR["etiqueta"],
                           promo_vence_txt=f"{_v.day} de {_meses_es[_v.month]}",
                           promo_dias=max(0, (_v - date.today()).days),
                           logueado=u is not None,
                           pago=_CFG_PRECIOS.get("pago", {}),
                           descarga_url=DESCARGA_LECTOR_PUBLICA,
                           whatsapp=re.sub(r"\D", "", str(_CONTACTO.get("whatsapp", ""))),
                           chat_ia_contador=asistente_ia_activo(IA_CFG),
                           chat_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""),
                           chat_ia_nombre=IA_CFG.get("nombre_asistente", "Asistente Contadores"))


def _info_contador_ia() -> str:
    """Datos vigentes (precios del pase de temporada y planes del Lector) que el
    asistente en modo contador puede citar, para no hardcodear cifras en el prompt."""
    cont = _CFG_PRECIOS.get("contadores", {})
    pase = cont.get("precio", 149900)
    temporada = cont.get("temporada", "")
    def _cop(n):
        return ("$" + f"{int(n):,}").replace(",", ".")
    lineas = ["\n\nDATOS VIGENTES QUE PUEDES CITAR (no inventes otros precios ni beneficios):"]
    lineas.append(f"- Prueba gratis: 1 declaración de muestra por contador (Formulario 210 + papeles "
                  f"de trabajo con marca de agua 'MUESTRA').")
    lineas.append(f"- Pase de temporada del liquidador de renta: {_cop(pase)} COP, pago único por toda "
                  f"la temporada {temporada}, con declaraciones ILIMITADAS.")
    lineas.append("- Planes del Lector XML DIAN (suscripción; el anual equivale a 2 meses gratis):")
    try:
        promo = _promo_lector_activa()
        for t in TIERS_LECTOR:
            c = t["clave"]
            pm = precio_lector(f"{c}_mensual")
            pa = precio_lector(f"{c}_anual")
            emp = "empresas ilimitadas" if not t["empresas_max"] else f"hasta {t['empresas_max']} empresas"
            lineas.append(f"    · {t['nombre']} ({emp}): {_cop(pm)}/mes o {_cop(pa)}/año.")
        if promo:
            _v = PROMO_LECTOR["vence"]
            lineas.append(f"- Promo vigente: {PROMO_LECTOR['etiqueta']} (hasta {_v.day}/{_v.month}/{_v.year}).")
    except Exception:
        lineas.append("    · Consulta los planes exactos en la página /contadores/lector.")
    return "\n".join(lineas)


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
                           bono_pase=_canjear_bono("contadores")[1],
                           logueado=u is not None,
                           muestra_usada=muestra_usada,
                           pago=PAGO,
                           ia_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""),
                           chat_ia_contador=asistente_ia_activo(IA_CFG),
                           chat_whatsapp=IA_CFG.get("negocio", {}).get("whatsapp", ""),
                           chat_ia_nombre=IA_CFG.get("nombre_asistente", "Asistente Contadores"))


@app.post("/api/pase-contador/crear")
def crear_pase_contador():
    """Crea la orden del pase de temporada de un contador (sin exógena y SIN
    registro): el contador solo deja su correo, que es con el que se le habilita
    al confirmar el pago."""
    u = usuario_actual()
    cont = _CFG_PRECIOS.get("contadores", {})
    cuerpo = request.get_json(silent=True) or {}
    email = (cuerpo.get("email") or (getattr(u, "email", "") if u else "") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Escribe un correo válido para enviarte el pase."}), 400
    nombre = (str(cuerpo.get("nombre", "")).strip()
              or (getattr(u, "nombre", "") if u else "") or "Contador")
    contacto = {"email": email, "nombre": nombre,
                "telefono": str(cuerpo.get("telefono", "")).strip()}
    orden_id = uuid.uuid4().hex[:12]
    bono_cod, bono_desc = _canjear_bono("contadores")
    ordenes = _leer_ordenes()
    ordenes[orden_id] = {
        "tipo": "orden", "plan": "contadores", "origen": _origen_actual(),
        "bono": bono_cod, "descuento": bono_desc,
        "precio": max(cont.get("precio", 149900) - bono_desc, 0), "contacto": contacto,
        "estado": "pendiente_pago", "fecha": str(date.today()),
        "nit": "", "nombre": nombre,
    }
    _guardar_ordenes(ordenes)
    from src import payu as _payu_mod
    from src import epayco as _epayco_mod
    pago_url = f"/pagar/{orden_id}" if (_epayco_mod.activo(EPAYCO) or _payu_mod.activo()) else ""
    return jsonify({"orden_id": orden_id, "precio": ordenes[orden_id]["precio"],
                    "descuento": bono_desc, "pago_url": pago_url})


# Precios de la suscripción al Lector XML (Tributando Contadores). Plana,
# empresas ILIMITADAS, por debajo de Kontalid ($297.700/año). Editable;
# idealmente mover a config/precios.yaml bloque `lector`.
PRECIOS_LECTOR = {
    # 3 planes. Anual = 10× el mensual (2 meses gratis). Precios alineados al
    # mercado (Kontalid Premium ~$297.700/año), con premium por generar planos
    # Siigo/Contai y llenar los formularios 300/350 oficiales.
    "independiente_mensual": 80000,   "independiente_anual":  800000,
    "pro_mensual":          150000,   "pro_anual":           1500000,
    "max_mensual":          200000,   "max_anual":           2000000,
    # Compat (suscripciones antiguas / enlaces viejos → equivalente más cercano).
    "contador_mensual":     150000,   "contador_anual":      1500000,
    "estudio_mensual":      150000,   "estudio_anual":       1500000,
    "ilimitado_mensual":    200000,   "ilimitado_anual":     2000000,
    "mensual": 80000, "anual": 800000,
}

# --- Promo de lanzamiento (Temporada de Renta 2026) ---
# Descuento por tiempo limitado en los planes ANUALES + agente IA de regalo en el
# anual Independiente. Después de `vence` los precios vuelven solos al normal.
PROMO_LECTOR = {
    "activa": True,
    "vence": date(2026, 8, 31),
    "etiqueta": "Promo de lanzamiento · Agente IA GRATIS",
    # Sin descuento de precio: la promo es el agente IA de regalo por tiempo
    # limitado. Los precios quedan en los normales (PRECIOS_LECTOR).
    "precios": {},
}

# Planes que incluyen el agente IA sin costo extra.
_PLANES_CON_AGENTE = {"pro_mensual", "pro_anual", "max_mensual", "max_anual"}


def _promo_lector_activa() -> bool:
    return bool(PROMO_LECTOR.get("activa")) and date.today() <= PROMO_LECTOR["vence"]


def precio_lector(plan: str) -> int:
    """Precio vigente del plan: promo si está activa, si no el normal."""
    if _promo_lector_activa() and plan in PROMO_LECTOR["precios"]:
        return PROMO_LECTOR["precios"][plan]
    return PRECIOS_LECTOR.get(plan, 0)


def plan_incluye_agente(plan: str) -> bool:
    """Pro y Max incluyen el agente IA. Durante la promo, también el anual
    Independiente (gancho de lanzamiento)."""
    if plan in _PLANES_CON_AGENTE:
        return True
    if _promo_lector_activa() and plan == "independiente_anual":
        return True
    return False


# Paquetes por # de empresas (para la página de precios).
TIERS_LECTOR = [
    {"clave": "independiente", "nombre": "Independiente", "empresas_max": 10},
    {"clave": "pro",           "nombre": "Pro",           "empresas_max": 25},
    {"clave": "max",           "nombre": "Max",           "empresas_max": 0},
]
# URL de descarga del instalador (tributando.co-Setup.exe). Subir el Setup al
# release de GitHub y poner el link acá (o en env DESCARGA_LECTOR).
DESCARGA_LECTOR_URL = os.environ.get(
    "DESCARGA_LECTOR",
    "https://github.com/ediandres07-oss/declaraci-n-de-renta/releases/download/v1.0/tributando.co.zip")

# URL pública y confiable que ve el contador (redirige al instalador real). Evita
# mostrar la URL cruda de GitHub, que rompe la confianza en una app que pide
# token/certificado de la DIAN. El día que montes descargas.tributando.co, solo
# cambias esta base.
_SITIO_PUB = (_CONTACTO.get("sitio") or "https://tributando.co").rstrip("/")
DESCARGA_LECTOR_PUBLICA = _SITIO_PUB + "/descargar-lector"


@app.get("/descargar-lector")
@app.get("/descargar")
def descargar_lector():
    """Enlace limpio de descarga del Lector: redirige al instalador real."""
    return redirect(DESCARGA_LECTOR_URL, code=302)


# ---------------------------------------------------------------------------
# Páginas SEO — para que un contador encuentre el Lector en Google.
# Cada URL es una landing con contenido único + enlaces internos + CTA a
# /contadores. Se renderizan con templates/seo.html (data-driven).
# ---------------------------------------------------------------------------
def _plano_seo(prog: str, slug: str) -> dict:
    return {
        "url": f"/{slug}",
        "title": f"Plano contable para {prog} desde los XML de la DIAN | Tributando.co",
        "meta": (f"Convierte los XML de la factura electrónica de la DIAN en el archivo plano "
                 f"listo para importar en {prog}, con el IVA discriminado. Sin digitar factura "
                 f"por factura. Prueba gratis para contadores."),
        "kick": "Plano contable",
        "h1": f"Genera el <span class='g'>plano contable para {prog}</span> desde los XML de la DIAN",
        "intro": (f"Con el Lector de Tributando descargas las facturas de la DIAN y obtienes el "
                  f"archivo plano listo para importar en {prog} —con el IVA ya discriminado— en "
                  f"minutos, no en horas."),
        "sections": [
            {"h2": f"De los XML de la DIAN al plano de {prog} en 3 pasos", "html": (
                "<ul class='chk'>"
                "<li>Conéctate a la DIAN con el token del correo y descarga tus facturas recibidas o emitidas.</li>"
                "<li>El Lector lee cada XML y discrimina el IVA, la retención y el tercero (NIT).</li>"
                f"<li>Genera el archivo plano con la estructura exacta de {prog} y lo importas.</li>"
                "</ul>")},
            {"h2": f"Por qué los contadores lo usan para {prog}", "html": (
                "<ul class='chk'>"
                "<li>Deja de digitar factura por factura: un solo cliente puede traer cientos de XML.</li>"
                "<li>El IVA descontable queda discriminado y cuadrado, tomado del XML.</li>"
                f"<li>La estructura del plano coincide con la que espera {prog}, sin re-armarla a mano.</li>"
                "<li>Ideal para causar compras y gastos y para preparar la información exógena.</li>"
                "</ul>")},
        ],
        "programas": True,
        "faq": [
            [f"¿El plano sirve para importar en {prog}?",
             f"Sí. El Lector arma el archivo plano con la estructura que {prog} espera, para que lo importes sin re-digitar."],
            ["¿Discrimina el IVA?",
             "Sí. Separa el IVA de cada factura (incluidas tarifas diferentes) y la retención, tomando los datos directamente del XML."],
            ["¿Cómo descargo las facturas de la DIAN?",
             "Desde el Lector te conectas a la DIAN con el token del correo (el del botón verde) y descargas tus facturas por fecha o por CUFE."],
            ["¿Puedo probarlo gratis?",
             "Sí. La primera prueba es gratis; luego el pase de temporada te da uso ilimitado por un solo pago."],
        ],
        "cta_titulo": f"Tu plano para {prog}, en minutos",
    }


_SEO_PAGES = {
    "lector-xml-dian": {
        "url": "/lector-xml-dian",
        "title": "Lector de XML de la DIAN: tus facturas al plano contable en minutos | Tributando.co",
        "meta": ("Lee los XML de la factura electrónica de la DIAN y genera el plano contable con el "
                 "IVA discriminado, listo para Siigo, Contai, World Office o Helisa. Prueba gratis para contadores."),
        "kick": "Para contadores",
        "h1": "Lector de <span class='g'>XML de la DIAN</span> → tu plano contable en minutos",
        "intro": ("El Lector de Tributando descarga las facturas electrónicas de la DIAN, lee cada XML y "
                  "te arma el plano contable con el IVA discriminado, listo para tu programa contable. "
                  "Deja de digitar factura por factura."),
        "sections": [
            {"h2": "Qué hace el Lector de XML de la DIAN", "html": (
                "<ul class='chk'>"
                "<li>Se conecta a la DIAN y descarga tus facturas (recibidas y emitidas) por fecha o por CUFE.</li>"
                "<li>Lee el XML de cada factura y extrae proveedor, NIT, valores, IVA y retención.</li>"
                "<li>Discrimina el IVA descontable, incluso con varias tarifas en una misma factura.</li>"
                "<li>Genera el archivo plano listo para importar en Siigo, Contai, World Office o Helisa.</li>"
                "</ul>")},
            {"h2": "Cómo funciona, paso a paso", "html": (
                "<ul class='chk'>"
                "<li><b>1.</b> Solicita el token en la DIAN.</li>"
                "<li><b>2.</b> En el correo de la DIAN, copia el vínculo del botón verde y pégalo en el Lector.</li>"
                "<li><b>3.</b> Activa el token y descarga tus CUFEs; el Lector los lee al tiempo.</li>"
                "<li><b>4.</b> Genera el plano para tu programa contable con el IVA ya discriminado.</li>"
                "</ul>")},
            {"h2": "Para quién es", "html": (
                "<p>Para contadores y firmas que procesan la contabilidad de varios clientes y no quieren "
                "digitar cada factura a mano. Un solo cliente puede traer cientos de XML al mes: el Lector "
                "los convierte en el plano contable en minutos.</p>")},
        ],
        "programas": True,
        "faq": [
            ["¿Qué es un lector de XML de la DIAN?",
             "Es una herramienta que lee los archivos XML de las facturas electrónicas de la DIAN y extrae sus datos (IVA, retención, terceros) para llevarlos a la contabilidad sin digitarlos a mano."],
            ["¿Sirve para la información exógena?",
             "Sí. Al tener las compras y ventas con el IVA discriminado y el tercero identificado, facilita preparar los formatos de exógena."],
            ["¿Con qué programas contables funciona?",
             "Genera el plano para Siigo, Contai, World Office y Helisa. Si usas otro, escríbenos y lo agregamos."],
            ["¿Cuánto cuesta?",
             "La primera prueba es gratis. Después, el pase de temporada da uso ilimitado por un solo pago."],
        ],
        "cta_titulo": "Deja de digitar factura por factura",
    },
    "plano-contable-siigo": _plano_seo("Siigo", "plano-contable-siigo"),
    "plano-contable-contai": _plano_seo("Contai", "plano-contable-contai"),
    "plano-contable-world-office": _plano_seo("World Office", "plano-contable-world-office"),
    "plano-contable-helisa": _plano_seo("Helisa", "plano-contable-helisa"),
    "iva-descontable-xml-dian": {
        "url": "/iva-descontable-xml-dian",
        "title": "IVA descontable desde los XML de la DIAN: cómo extraerlo | Tributando.co",
        "meta": ("Extrae el IVA descontable de las facturas electrónicas de la DIAN directamente del XML, "
                 "discriminado por tarifa, sin digitar. Listo para tu contabilidad y la exógena."),
        "kick": "IVA descontable",
        "h1": "<span class='g'>IVA descontable</span> desde los XML de la DIAN, sin digitar",
        "intro": ("El Lector lee el XML de cada factura y separa el IVA descontable —incluso con varias "
                  "tarifas en una misma factura— junto con la retención y el tercero, para que llegue "
                  "cuadrado a tu contabilidad."),
        "sections": [
            {"h2": "El IVA sale del XML, no a mano", "html": (
                "<ul class='chk'>"
                "<li>Toma el valor del impuesto tal cual viene en el XML de la DIAN.</li>"
                "<li>Discrimina IVA al 19%, 5% y exento, y la retención en la fuente.</li>"
                "<li>Identifica el tercero (NIT) de cada factura.</li>"
                "<li>Lo entrega en el plano listo para importar en tu programa contable.</li>"
                "</ul>")},
            {"h2": "Por qué importa para el contador", "html": (
                "<p>Digitar el IVA factura por factura es lento y propenso a errores. Al leerlo del XML, el "
                "IVA descontable queda exacto y cuadrado, lo que agiliza la causación y la preparación de "
                "la información exógena.</p>")},
        ],
        "programas": True,
        "faq": [
            ["¿De dónde saca el IVA?",
             "Directamente del XML de la factura electrónica de la DIAN, que es el documento con validez fiscal."],
            ["¿Maneja varias tarifas de IVA?",
             "Sí. Discrimina las distintas tarifas (19%, 5%, exento) dentro de una misma factura."],
            ["¿Incluye la retención?",
             "Sí, además del IVA separa la retención en la fuente cuando viene en el documento."],
        ],
        "cta_titulo": "Tu IVA descontable, cuadrado y sin digitar",
    },
    "descargar-xml-dian": {
        "url": "/descargar-xml-dian",
        "title": "Descargar los XML de la DIAN masivamente (con token) | Tributando.co",
        "meta": ("Descarga tus facturas electrónicas de la DIAN de forma masiva con el token, por fecha o "
                 "por CUFE, y léelas al tiempo para armar el plano contable. Prueba gratis."),
        "kick": "Descarga masiva",
        "h1": "Descarga los <span class='g'>XML de la DIAN</span> masivamente, con el token",
        "intro": ("Con el Lector te conectas a la DIAN usando el token del correo y descargas todas tus "
                  "facturas de una —por rango de fechas o pegando los CUFEs— para leerlas al tiempo."),
        "sections": [
            {"h2": "Cómo descargar los XML de la DIAN", "html": (
                "<ul class='chk'>"
                "<li>Solicita el token en la DIAN.</li>"
                "<li>Copia el vínculo del botón verde del correo de la DIAN y pégalo en el Lector.</li>"
                "<li>Activa el token y descarga tus facturas recibidas o emitidas.</li>"
                "<li>El Lector las lee al tiempo y arma el plano contable con el IVA discriminado.</li>"
                "</ul>")},
            {"h2": "Por fecha o pegando los CUFEs", "html": (
                "<p>Puedes traer las facturas por un rango de fechas o pegar directamente los CUFEs que "
                "necesites. Ideal cuando un cliente te entrega un listado y quieres procesarlo de una.</p>")},
        ],
        "programas": True,
        "faq": [
            ["¿Necesito el token de la DIAN?",
             "Sí. El token es el que la DIAN te envía por correo (el del botón verde); con él el Lector se conecta y descarga tus facturas."],
            ["¿Puedo descargar muchas facturas a la vez?",
             "Sí. La descarga es masiva: por rango de fechas o pegando los CUFEs, y se leen todas al tiempo."],
            ["¿Después qué obtengo?",
             "El plano contable listo para tu programa (Siigo, Contai, World Office o Helisa), con el IVA discriminado."],
        ],
        "cta_titulo": "Descarga y lee tus facturas de la DIAN de una",
    },
}


def _seo_view(slug):
    """Landings SEO para contadores (ver _SEO_PAGES)."""
    p = _SEO_PAGES.get(slug)
    if not p:
        abort(404)
    return render_template("seo.html", p=p)


for _seo_slug in _SEO_PAGES:
    app.add_url_rule("/" + _seo_slug,
                     endpoint="seo_" + _seo_slug.replace("-", "_"),
                     view_func=(lambda s=_seo_slug: _seo_view(s)))

# Última versión publicada del Lector. Súbela cada vez que recompiles y publiques
# un instalador nuevo; el Lector la consulta y avisa al contador si está atrasado.
LECTOR_VERSION_LATEST = os.environ.get("LECTOR_VERSION", "1.2.3")
# Novedades de la última versión (se muestran en el banner "¿Qué hay de nuevo?"
# dentro del Lector). Se puede sobrescribir con la variable de entorno del mismo
# nombre sin recompilar el servidor. Usa saltos de línea para viñetas.
LECTOR_NOVEDADES = os.environ.get("LECTOR_NOVEDADES",
    "• Lee balances de Helisa (.xls).\n"
    "• 7 sistemas contables para elegir: Siigo, World Office, Helisa, Genérico, Alegra, Loggro y Contai.\n"
    "• Corregir una cuenta del catálogo ahora sí se refleja en el plano.\n"
    "• Pantalla de entrada renovada.")


@app.post("/api/lector-suscripcion/crear")
@login_requerido
def crear_suscripcion_lector():
    """Crea la orden de suscripción al Lector XML. Al confirmar el pago se crea
    la suscripción y se entrega la clave de licencia al correo del contador."""
    u = usuario_actual()
    cuerpo = request.get_json(silent=True) or {}
    plan = (cuerpo.get("plan") or "independiente").lower()
    if plan not in PRECIOS_LECTOR:
        return jsonify({"error": "Plan inválido."}), 400
    email = (u.email or "").strip()
    if not email:
        return jsonify({"error": "Tu cuenta no tiene correo."}), 400
    precio = precio_lector(plan)
    orden_id = uuid.uuid4().hex[:12]
    ordenes = _leer_ordenes()
    ordenes[orden_id] = {
        "tipo": "orden", "plan": "lector", "plan_lector": plan, "origen": _origen_actual(),
        "precio": precio,
        "contacto": {"email": email, "nombre": (u.nombre or "").strip(),
                     "telefono": str(cuerpo.get("telefono", "")).strip()},
        "estado": "pendiente_pago", "fecha": str(date.today()),
        "nit": "", "nombre": (u.nombre or "Contador"),
    }
    _guardar_ordenes(ordenes)
    from src import payu as _payu_mod
    from src import epayco as _epayco_mod
    hay_pago = _epayco_mod.activo(EPAYCO) or _payu_mod.activo()
    pago_url = f"/pagar/{orden_id}" if hay_pago else ""
    return jsonify({"orden_id": orden_id, "precio": precio, "plan": plan, "pago_url": pago_url})


def _base_url_publica() -> str:
    """URL pública (https) para las callbacks de PayU."""
    from src import payu as _payu_mod
    cfg = _payu_mod.cargar_config()
    if cfg.get("base_url"):
        return str(cfg["base_url"]).rstrip("/")
    return request.host_url.rstrip("/").replace("http://", "https://")


def _descripcion_orden(orden: dict) -> str:
    """Texto para la pasarela según el producto de la orden."""
    plan = orden.get("plan", "")
    if plan == "lector":
        return f"Suscripción Lector tributando.co — plan {orden.get('plan_lector', '')}"
    if plan == "contadores":
        return "Pase de temporada — tributando.co"
    return "Declaración de renta — tributando.co"


@app.get("/pagar/<orden_id>")
def pagar_orden(orden_id):
    """Abre el pago en línea de CUALQUIER orden (Lector, pase, renta).
    Usa ePayco si está activo; si no, PayU."""
    from src import payu as _payu_mod
    from src import epayco as _epayco_mod
    orden = _leer_ordenes().get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return "Orden no encontrada.", 404
    desc = _descripcion_orden(orden)

    # --- ePayco (Checkout Standard con checkout.js) ---
    if _epayco_mod.activo(EPAYCO):
        if orden.get("estado") == "pagada":
            return redirect("/epayco/respuesta?ref=" + orden_id + "&ya=1")
        cont = orden.get("contacto") or {}
        d = _epayco_mod.datos_checkout(
            EPAYCO, orden_id, orden.get("precio", 0), desc,
            cont.get("email", ""), _base_url_publica(),
            nombre=cont.get("nombre", "") or orden.get("nombre", ""),
            telefono=cont.get("telefono", ""))
        d.update({"banco": PAGO.get("banco", ""), "tipo_cuenta": PAGO.get("tipo", ""),
                  "numero_cuenta": PAGO.get("numero", ""), "nequi": PAGO.get("nequi", "")})
        return render_template_string(_EPAYCO_CHECKOUT_HTML, d=d)

    # --- PayU (WebCheckout) ---
    cfg = _payu_mod.cargar_config()
    if not _payu_mod.activo(cfg):
        return "El pago en línea no está configurado. Escríbenos para activarte.", 503
    if orden.get("estado") == "pagada":
        return redirect("/payu/respuesta?ref=" + orden_id + "&ya=1")
    p = _payu_mod.parametros_checkout(
        orden_id, orden.get("precio", 0), desc,
        (orden.get("contacto") or {}).get("email", ""),
        _base_url_publica(), cfg)
    campos = "".join(
        f'<input type="hidden" name="{k}" value="{str(v).replace(chr(34), "&quot;")}">'
        for k, v in p["campos"].items())
    return (f'<!doctype html><html><body onload="document.forms[0].submit()">'
            f'<p style="font-family:sans-serif;text-align:center;margin-top:40px">'
            f'Redirigiéndote al pago seguro de PayU…</p>'
            f'<form method="post" action="{p["url"]}">{campos}</form></body></html>')


@app.post("/payu/confirmacion")
def payu_confirmacion():
    """Webhook servidor-a-servidor de PayU. Si el pago aprobó, crea la suscripción
    y entrega la licencia por correo. Siempre responde 200 (PayU reintenta si no)."""
    from src import payu as _payu_mod
    try:
        params = request.form.to_dict() or {}
        cfg = _payu_mod.cargar_config()
        if not _payu_mod.confirmacion_valida(params, cfg):
            app.logger.warning("PayU: firma de confirmación inválida (%s)", params.get("reference_sale"))
            return "ok", 200
        orden_id = params.get("reference_sale", "")
        ordenes = _leer_ordenes()
        orden = ordenes.get(orden_id)
        if not orden or orden.get("tipo") != "orden":
            return "ok", 200
        if _payu_mod.aprobada(params) and orden.get("estado") != "pagada":
            _finalizar_pago_orden(orden_id, orden, ordenes)   # activa el producto (lector/pase/renta)
            _guardar_ordenes(ordenes)
        elif not _payu_mod.aprobada(params):
            orden["estado"] = "pago_" + _payu_mod.estado_texto(params)
            _guardar_ordenes(ordenes)
    except Exception as e:
        app.logger.warning("PayU confirmación: %s", e)
    return "ok", 200


@app.route("/payu/respuesta")
def payu_respuesta():
    """Página que ve el contador al volver de PayU (el estado real lo fija el
    webhook; aquí solo mostramos un mensaje)."""
    ref = request.args.get("ref") or request.args.get("referenceCode", "")
    estado = (request.args.get("lapTransactionState") or "").upper()
    ok = request.args.get("ya") == "1" or estado == "APPROVED"
    orden = _leer_ordenes().get(ref, {})
    pagada = ok or orden.get("estado") == "pagada"
    titulo = "¡Pago confirmado!" if pagada else ("Pago " + (estado.lower() or "en proceso"))
    msg = ("Tu suscripción quedó activa. Te enviamos la clave y el enlace de descarga a tu correo. "
           "También puedes entrar al Lector con tu correo (te llega un código)."
           if pagada else
           "Si el pago se completó, en unos minutos recibirás la activación por correo. "
           "Si fue rechazado, puedes intentar de nuevo.")
    valor = orden.get("precio", 0) or 0
    return render_template_string(
        "<!doctype html><html><head><meta charset='utf-8'><title>{{t}}</title>"
        "{% if pagada and valor %}"
        "<script async src='https://www.googletagmanager.com/gtag/js?id=AW-18353583911'></script>"
        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
        "gtag('js', new Date());gtag('config','AW-18353583911');"
        "gtag('event','conversion',{'send_to':'AW-18353583911/BrvcCLXeodccEKfu1a9E',"
        "'value':{{ valor }},'currency':'COP','transaction_id':'{{ ref }}'});</script>"
        "{% endif %}"
        "</head>"
        "<body style='font-family:sans-serif;max-width:520px;margin:60px auto;text-align:center;color:#1e2432'>"
        "<div style='font-size:52px'>{{ '✅' if pagada else '⏳' }}</div>"
        "<h1 style='color:#1e2432'>{{t}}</h1><p style='color:#5a6b7f'>{{m}}</p>"
        "{% if rechazada %}"
        "<a href='/pagar/{{ ref }}' style='display:inline-block;margin-top:16px;background:#1b7f4b;color:#fff;"
        "padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:700'>🔄 Intentar el pago de nuevo</a>"
        "<div style='margin:14px auto;max-width:420px;background:#faf7f0;border:1px dashed #c8991f;"
        "border-radius:12px;padding:14px;font-size:.9rem;text-align:left'><b>O paga por transferencia:</b><br>"
        "{{ banco }} {{ tipo_cuenta }} <b>{{ numero_cuenta }}</b>{% if nequi %} · Nequi <b>{{ nequi }}</b>{% endif %}<br>"
        "<div style='text-align:center;margin:10px 0'><img src='/static/img/qr-bre-b-solo.png?v=1' "
        "alt='QR Bre-B para pagar desde Nequi o tu banco' style='width:260px;max-width:90%;height:auto;border-radius:10px'><br>"
        "<span style='font-size:.8rem;color:#8a919c'>Escanea desde Nequi o tu app bancaria (Bre-B)<br>"
        "o paga a la llave Bre-B <b>0093134158</b> (Tributando · Nequi Negocios)</span></div>"
        "<span style='color:#8a919c'>Valor exacto y tu número de orden <b>{{ ref }}</b> como referencia.</span>"
        "<div style='text-align:center;margin-top:10px'><button onclick=\"fetch('/api/reportar-pago',{method:'POST',"
        "headers:{'Content-Type':'application/json'},body:JSON.stringify({orden_id:'{{ ref }}'})})"
        ".then(r=>{this.disabled=true;document.getElementById('ypMsg').textContent=r.ok?'✓ Aviso enviado. Verificamos y te activamos por correo.':'No se pudo, escríbenos por WhatsApp.'})\" "
        "style='background:#1b7f4b;color:#fff;border:0;border-radius:9px;padding:10px 18px;font-weight:700;"
        "cursor:pointer'>✅ Ya transferí — avisar</button>"
        "<div id='ypMsg' style='font-size:.85rem;color:#1b7f4b;margin-top:6px'></div></div></div><br>"
        "{% endif %}"
        "<a href='/contadores/lector' style='display:inline-block;margin-top:16px;background:#c8991f;color:#fff;"
        "padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600'>Volver</a></body></html>",
        t=titulo, m=msg, pagada=pagada, rechazada=rechazada, valor=valor, ref=ref,
        banco=PAGO.get("banco", ""), tipo_cuenta=PAGO.get("tipo", ""),
        numero_cuenta=PAGO.get("numero", ""), nequi=PAGO.get("nequi", ""))


def _enviar_licencia_lector(email: str, plan: str, licencia: str):
    """Correo de bienvenida con la licencia y el enlace de descarga."""
    from src.correo import enviar_email
    html = (
        "<div style='font-family:sans-serif;max-width:520px;margin:auto'>"
        "<h2 style='color:#1e2432'>¡Bienvenido al Lector de tributando.co!</h2>"
        f"<p>Tu suscripción <b>{plan.upper()}</b> quedó activa. 🎉</p>"
        "<p><b>1.</b> Descarga el Lector (Windows):</p>"
        f"<p><a href='{DESCARGA_LECTOR_PUBLICA}' style='background:#1e2432;color:#fff;padding:10px 18px;"
        "border-radius:8px;text-decoration:none'>Descargar el Lector (ZIP)</a></p>"
        "<p><b>2.</b> <b>Descomprime</b> el ZIP (clic derecho → Extraer todo) y abre "
        "<b>tributando.co.exe</b> (dentro de la carpeta).</p>"
        "<p><b>3.</b> Entra con <b>este mismo correo</b> — te llegará un código de 6 dígitos.</p>"
        f"<p style='color:#7b7568;font-size:.9rem'>Clave de respaldo (por si la necesitas): "
        f"<b>{licencia}</b></p></div>")
    enviar_email(email, "Tu suscripción al Lector de tributando.co está activa", html)


_EPAYCO_CHECKOUT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<title>Pago seguro — ePayco</title></head>
<body style="font-family:sans-serif;text-align:center;margin-top:60px;color:#1e2432">
<p id="msg">Abriendo el pago seguro de ePayco…</p>
<button id="btnPagar" style="display:none;background:#c8991f;color:#fff;border:0;border-radius:10px;
  padding:14px 30px;font-weight:800;font-size:1rem;cursor:pointer">Abrir el pago seguro</button>
<p style="color:#8a919c;font-size:.85rem;max-width:420px;margin:18px auto">Si tu banco rechaza el
  primer intento, vuelve a intentarlo: es una verificación normal de seguridad y el segundo
  intento suele pasar sin problema.</p>
<div style="max-width:420px;margin:10px auto;background:#faf7f0;border:1px dashed #c8991f;border-radius:12px;padding:14px;font-size:.9rem;text-align:left">
  <b>¿Prefieres pagar por transferencia?</b><br>
  {{ d.banco }} {{ d.tipo_cuenta }} <b>{{ d.numero_cuenta }}</b>{% if d.nequi %} · Nequi <b>{{ d.nequi }}</b>{% endif %}<br>
  <div style="text-align:center;margin:10px 0"><img src="/static/img/qr-bre-b-solo.png?v=1"
    alt="QR Bre-B para pagar desde Nequi o tu banco" style="width:260px;max-width:90%;height:auto;border-radius:10px"><br>
    <span style="font-size:.8rem;color:#8a919c">Escanea desde Nequi o tu app bancaria (Bre-B)<br>
    o paga a la llave Bre-B <b>0093134158</b> (Tributando · Nequi Negocios)</span></div>
  <span style="color:#8a919c">Transfiere el valor exacto y usa tu número de orden
  <b>{{ d.invoice }}</b> como referencia.</span>
  <div style="text-align:center;margin-top:10px">
    <button id="btnYaPague" onclick="reportarPago()" style="background:#1b7f4b;color:#fff;border:0;
      border-radius:9px;padding:10px 18px;font-weight:700;cursor:pointer">✅ Ya transferí — avisar</button>
    <div id="yaPagueMsg" style="font-size:.85rem;color:#1b7f4b;margin-top:6px"></div>
  </div>
</div>
<script>
async function reportarPago(){
  const b = document.getElementById("btnYaPagué") || document.getElementById("btnYaPague");
  b.disabled = true;
  try {
    const r = await fetch("/api/reportar-pago", {method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify({orden_id: "{{ d.invoice }}"})});
    document.getElementById("yaPagueMsg").textContent = r.ok
      ? "✓ Aviso enviado. Verificamos tu pago y te activamos por correo."
      : "No se pudo enviar el aviso, escríbenos por WhatsApp.";
  } catch(e){ document.getElementById("yaPagueMsg").textContent = "Sin conexión, intenta de nuevo."; b.disabled = false; }
}
</script>
<script src="https://checkout.epayco.co/checkout.js"></script>
<script>
  var handler = ePayco.checkout.configure({ key: "{{ d.public_key }}", test: {{ d.test }} });
  function abrirPago(){
    handler.open({
      name: "{{ d.name }}", description: "{{ d.description }}", invoice: "{{ d.invoice }}",
      currency: "{{ d.currency }}", amount: "{{ d.amount }}", country: "{{ d.country }}",
      external: "false", email_billing: "{{ d.email_billing }}",
      name_billing: "{{ d.name_billing }}", mobilephone_billing: "{{ d.mobilephone_billing }}",
      extra1: "{{ d.extra1 }}",
      response: "{{ d.response }}", confirmation: "{{ d.confirmation }}"
    });
  }
  var btn = document.getElementById("btnPagar");
  btn.onclick = abrirPago;
  abrirPago();
  // Si el navegador bloqueó la apertura automática, mostrar el botón como plan B.
  setTimeout(function(){ btn.style.display = "inline-block";
    document.getElementById("msg").textContent = "Si no se abrió la ventana de pago, tócala aquí:"; }, 3500);
</script></body></html>"""


@app.route("/epayco/confirmacion", methods=["GET", "POST"])
def epayco_confirmacion():
    """Webhook de ePayco. Si el pago fue Aceptado, crea la suscripción y entrega
    la licencia por correo. Siempre responde 200."""
    from src import epayco as _epayco_mod
    try:
        params = request.form.to_dict() or request.args.to_dict() or {}
        if not _epayco_mod.verificar_firma(params, EPAYCO):
            app.logger.warning("ePayco: firma inválida (%s)", params.get("x_id_invoice"))
            return "ok", 200
        orden_id = params.get("x_id_invoice") or params.get("x_extra1", "")
        ordenes = _leer_ordenes()
        orden = ordenes.get(orden_id)
        if not orden or orden.get("tipo") != "orden":
            return "ok", 200
        if _epayco_mod.aprobada(params) and orden.get("estado") != "pagada":
            _finalizar_pago_orden(orden_id, orden, ordenes)   # activa el producto (lector/pase/renta)
            _guardar_ordenes(ordenes)
        elif not _epayco_mod.aprobada(params):
            orden["estado"] = "pago_" + _epayco_mod.estado_texto(params)
            _guardar_ordenes(ordenes)
    except Exception as e:
        app.logger.warning("ePayco confirmación: %s", e)
    return "ok", 200


@app.route("/epayco/respuesta")
def epayco_respuesta():
    """Página que ve el contador al volver de ePayco."""
    ref = request.args.get("ref") or request.args.get("x_id_invoice", "")
    ok = request.args.get("ya") == "1"
    orden = _leer_ordenes().get(ref, {})
    pagada = ok or orden.get("estado") == "pagada"
    rechazada = str(orden.get("estado", "")).startswith("pago_rechazada")
    titulo = ("¡Pago confirmado!" if pagada else
              "El banco no aprobó este intento" if rechazada else "Pago en proceso")
    msg = ("Tu suscripción quedó activa. Te enviamos la clave y el enlace de descarga a tu correo. "
           "También puedes entrar al Lector con tu correo (te llega un código)."
           if pagada else
           "Tranquilo: es una verificación de seguridad normal en el primer intento y NO se hizo "
           "ningún cobro. Vuelve a intentarlo — el segundo intento casi siempre pasa."
           if rechazada else
           "Si el pago se completó, en unos minutos recibirás la activación por correo.")
    valor = orden.get("precio", 0) or 0
    return render_template_string(
        "<!doctype html><html><head><meta charset='utf-8'><title>{{t}}</title>"
        "{% if pagada and valor %}"
        "<script async src='https://www.googletagmanager.com/gtag/js?id=AW-18353583911'></script>"
        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
        "gtag('js', new Date());gtag('config','AW-18353583911');"
        "gtag('event','conversion',{'send_to':'AW-18353583911/BrvcCLXeodccEKfu1a9E',"
        "'value':{{ valor }},'currency':'COP','transaction_id':'{{ ref }}'});</script>"
        "{% endif %}"
        "</head>"
        "<body style='font-family:sans-serif;max-width:520px;margin:60px auto;text-align:center;color:#1e2432'>"
        "<div style='font-size:52px'>{{ '✅' if pagada else '⏳' }}</div>"
        "<h1 style='color:#1e2432'>{{t}}</h1><p style='color:#5a6b7f'>{{m}}</p>"
        "{% if rechazada %}"
        "<a href='/pagar/{{ ref }}' style='display:inline-block;margin-top:16px;background:#1b7f4b;color:#fff;"
        "padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:700'>🔄 Intentar el pago de nuevo</a>"
        "<div style='margin:14px auto;max-width:420px;background:#faf7f0;border:1px dashed #c8991f;"
        "border-radius:12px;padding:14px;font-size:.9rem;text-align:left'><b>O paga por transferencia:</b><br>"
        "{{ banco }} {{ tipo_cuenta }} <b>{{ numero_cuenta }}</b>{% if nequi %} · Nequi <b>{{ nequi }}</b>{% endif %}<br>"
        "<div style='text-align:center;margin:10px 0'><img src='/static/img/qr-bre-b-solo.png?v=1' "
        "alt='QR Bre-B para pagar desde Nequi o tu banco' style='width:260px;max-width:90%;height:auto;border-radius:10px'><br>"
        "<span style='font-size:.8rem;color:#8a919c'>Escanea desde Nequi o tu app bancaria (Bre-B)<br>"
        "o paga a la llave Bre-B <b>0093134158</b> (Tributando · Nequi Negocios)</span></div>"
        "<span style='color:#8a919c'>Valor exacto y tu número de orden <b>{{ ref }}</b> como referencia.</span>"
        "<div style='text-align:center;margin-top:10px'><button onclick=\"fetch('/api/reportar-pago',{method:'POST',"
        "headers:{'Content-Type':'application/json'},body:JSON.stringify({orden_id:'{{ ref }}'})})"
        ".then(r=>{this.disabled=true;document.getElementById('ypMsg').textContent=r.ok?'✓ Aviso enviado. Verificamos y te activamos por correo.':'No se pudo, escríbenos por WhatsApp.'})\" "
        "style='background:#1b7f4b;color:#fff;border:0;border-radius:9px;padding:10px 18px;font-weight:700;"
        "cursor:pointer'>✅ Ya transferí — avisar</button>"
        "<div id='ypMsg' style='font-size:.85rem;color:#1b7f4b;margin-top:6px'></div></div></div><br>"
        "{% endif %}"
        "<a href='/contadores/lector' style='display:inline-block;margin-top:16px;background:#c8991f;color:#fff;"
        "padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600'>Volver</a></body></html>",
        t=titulo, m=msg, pagada=pagada, rechazada=rechazada, valor=valor, ref=ref,
        banco=PAGO.get("banco", ""), tipo_cuenta=PAGO.get("tipo", ""),
        numero_cuenta=PAGO.get("numero", ""), nequi=PAGO.get("nequi", ""))


@app.post("/api/muestra/codigo")
def api_muestra_codigo():
    """Envía un código de 6 dígitos al correo para verificarlo ANTES de descargar
    la muestra (sin registro). Si ese correo ya usó su muestra, lo avisa."""
    from src.auth import MuestraContadorEmail, generar_codigo_muestra
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Escribe un correo válido."}), 400
    if db.session.get(MuestraContadorEmail, email) is not None:
        # Ya conoció la herramienta: en vez de solo rechazarlo, se le ofrece el
        # bono de $30.000 para el pase (se reusa si ya tiene uno vigente).
        bono_cod = ""
        try:
            from src.auth import Bono
            from src.gerente import _crear_bono
            bx = (Bono.query.filter_by(email=email, tipo="pase", usado=False)
                  .filter(Bono.expira > datetime.utcnow()).first())
            if bx is None:
                bx = db.session.get(Bono, _crear_bono(email, "pase"))
            bono_cod = bx.codigo
            session["bono"] = bono_cod
        except Exception:
            app.logger.warning("muestra/codigo: no se pudo crear bono de recompra")
        return jsonify({"ok": False, "error": "Ese correo ya usó su muestra gratis. "
                        "Activa tu pase de temporada para declaraciones ilimitadas.",
                        "bono": bono_cod, "descuento": 30000}), 402
    codigo = generar_codigo_muestra(email)
    try:
        from src.auth import CodigoMuestra
        fila = db.session.get(CodigoMuestra, email)
        if fila is not None and not fila.origen:
            fila.origen = _origen_actual()
            db.session.commit()
    except Exception:
        db.session.rollback()
    # Bono de bienvenida del contador: se entrega al dejar el correo y se
    # descuenta solo cuando compra el pase (un bono por correo).
    bono_cod = ""
    try:
        from src.auth import Bono
        from src.gerente import _crear_bono
        b = (Bono.query.filter_by(email=email, tipo="pase", usado=False)
             .filter(Bono.expira > datetime.utcnow()).first())
        if b is None:
            b = db.session.get(Bono, _crear_bono(email, "pase"))
        bono_cod = b.codigo
        session["bono"] = bono_cod
    except Exception:
        app.logger.warning("muestra/codigo: no se pudo crear el bono")
    html = (
        "<div style='font-family:sans-serif;max-width:460px;margin:auto'>"
        "<h2 style='color:#1e2432'>Tu código para la muestra</h2>"
        "<p>Escríbelo en tributando.co/contadores para descargar tu muestra gratis:</p>"
        f"<p style='font-size:34px;font-weight:700;letter-spacing:6px;color:#c8991f;margin:18px 0'>{codigo}</p>"
        "<p style='color:#7b7568;font-size:.9rem'>Vence en 15 minutos.</p></div>"
    )
    try:
        from src.correo import enviar_email
        enviar_email(email, "Tu código de muestra — Tributando.co", html)
    except Exception as e:
        app.logger.warning("muestra código: no se pudo enviar: %s", e)
        return jsonify({"ok": False, "error": "No pudimos enviar el correo. Intenta de nuevo."}), 502
    return jsonify({"ok": True, "mensaje": "Te enviamos un código a tu correo.",
                    "bono": bono_cod, "descuento": 30000})


@app.get("/api/muestra-contador/<token>.pdf")
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

    from src.auth import MuestraContadorEmail, registrar_muestra_email
    email = (request.args.get("email") or (getattr(u, "email", "") if u else "") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Escribe un correo válido para descargar tu muestra."}), 400
    from src.auth import verificar_codigo_muestra
    if not verificar_codigo_muestra(email, (request.args.get("codigo") or "").strip()):
        return jsonify({"error": "Verifica tu correo: pide y escribe el código de 6 dígitos."}), 401
    previa = db.session.get(MuestraContadorEmail, email)
    if previa is not None and (previa.token or "") != token:
        return jsonify({"error": "Ya usaste tu declaración de muestra gratis con ese correo. "
                        "Activa tu pase de temporada para ilimitadas."}), 402

    liq = calcular(datos, PARAMS)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        generar_formulario_pdf(salida, datos, liq, PARAMS, marca="MUESTRA · TRIBUTANDO.CO")
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    registrar_muestra_email(email, token=token, nit=carga.get("nit", ""),
                            nombre=(getattr(u, "nombre", "") if u else ""))
    _sellar_origen_muestra(email)

    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name=f"MUESTRA_Formulario210_{carga.get('nit','')}.pdf",
                     mimetype="application/pdf")


_WM_MUESTRA = BASE / "static" / "img" / "wm_muestra.png"


def _marcar_muestra_excel(ruta):
    """Marca de agua 'MUESTRA' en CADA hoja del Excel de papeles: imagen diagonal
    sobre los datos + texto en encabezado/pie (se ve en pantalla e impresión).
    No mueve los datos."""
    import openpyxl
    import warnings as _w
    from openpyxl.drawing.image import Image as _XLImage
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        wb = openpyxl.load_workbook(ruta)
    for ws in wb.worksheets:
        try:
            ws.oddHeader.center.text = "MUESTRA · TRIBUTANDO.CO — no válido para presentar en firme"
            ws.oddFooter.center.text = "Muestra gratuita · activa tu pase en tributando.co/contadores"
        except Exception:
            pass
        try:
            if _WM_MUESTRA.exists():
                im = _XLImage(str(_WM_MUESTRA))   # una instancia por hoja
                im.anchor = "B3"
                ws.add_image(im)
        except Exception:
            pass
    wb.save(ruta)


@app.get("/api/muestra-contador/<token>.zip")
def muestra_contador_zip(token):
    """Entrega UNA vez, gratis, la MUESTRA COMPLETA: Formulario 210 (PDF con marca)
    + papeles de trabajo (Excel con marca). El límite (1 por usuario) vive en la BD;
    al pasar de 1 se bloquea y debe pedir el pase de temporada a Tributando."""
    import zipfile
    u = usuario_actual()
    carga = _leer_ordenes().get(token)
    if not carga or carga.get("tipo") != "carga":
        return jsonify({"error": "Sube primero una exógena."}), 400
    try:
        datos = DatosDeclaracion.from_dict(carga.get("datos", {}))
    except (TypeError, KeyError):
        return jsonify({"error": "No hay datos válidos para la muestra."}), 410

    from src.auth import MuestraContadorEmail, registrar_muestra_email
    email = (request.args.get("email") or (getattr(u, "email", "") if u else "") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"error": "Escribe un correo válido para descargar tu muestra."}), 400
    from src.auth import verificar_codigo_muestra
    if not verificar_codigo_muestra(email, (request.args.get("codigo") or "").strip()):
        return jsonify({"error": "Verifica tu correo: pide y escribe el código de 6 dígitos."}), 401
    previa = db.session.get(MuestraContadorEmail, email)
    if previa is not None and (previa.token or "") != token:
        return jsonify({"error": "Ya usaste tu declaración de muestra gratis con ese correo. "
                        "Pide tu pase de temporada a Tributando para ilimitadas."}), 402

    liq = calcular(datos, PARAMS)
    nit = carga.get("nit", "")

    # La exógena para llenar su hoja de detalle: en memoria, y si se perdió (disco
    # efímero de Render), se re-lee del archivo guardado en la BD.
    exogena = _exogena_de(token)
    if exogena is None:
        try:
            fila = _leer_archivo_bd(token)
            if fila is not None and getattr(fila, "datos", None):
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tex:
                    ruta_ex = Path(tex.name)
                ruta_ex.write_bytes(fila.datos)
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        exogena = parsear_exogena(ruta_ex)
                finally:
                    ruta_ex.unlink(missing_ok=True)
        except Exception:
            exogena = None

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as t1:
        ruta_pdf = Path(t1.name)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as t2:
        ruta_xlsx = Path(t2.name)
    try:
        generar_formulario_pdf(ruta_pdf, datos, liq, PARAMS, marca="MUESTRA · TRIBUTANDO.CO")
        pdf_bytes = ruta_pdf.read_bytes()
        escribir_formulario(PLANTILLA, ruta_xlsx, datos, liq, exogena)
        _marcar_muestra_excel(ruta_xlsx)
        xlsx_bytes = ruta_xlsx.read_bytes()
    finally:
        ruta_pdf.unlink(missing_ok=True)
        ruta_xlsx.unlink(missing_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"MUESTRA_Formulario210_{nit}.pdf", pdf_bytes)
        z.writestr(f"MUESTRA_Papeles_de_trabajo_{nit}.xlsx", xlsx_bytes)
    buf.seek(0)

    registrar_muestra_email(email, token=token, nit=nit,
                            nombre=(getattr(u, "nombre", "") if u else ""))
    _sellar_origen_muestra(email)

    return send_file(buf, as_attachment=True,
                     download_name=f"MUESTRA_Declaracion_{nit}.zip",
                     mimetype="application/zip")


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
    contenido_xlsx = ruta_tmp.read_bytes()
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
    try:
        _guardar_archivo_bd(token, f"Exogena_{exogena.identificacion or 'sin_nit'}.xlsx",
                            contenido_xlsx)
    except Exception:
        app.logger.warning("cargar: no se pudo persistir la exógena en BD")
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
            "fecha_limite": (lambda _fl: {
                "fecha": _fl.strftime("%d/%m/%Y") if _fl else None,
                "dias": (_fl - date.today()).days if _fl else None,
            })(fecha_limite(exogena.identificacion, PLANTILLA) if exogena.identificacion else None),
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
    exogena = _exogena_de(cuerpo.get("token", ""))
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


@app.post("/api/borrador-comerciante")
@pro_requerido
def borrador_comerciante():
    """Borrador de renta del comerciante (PN) en Excel: incluye la hoja
    'Comerciante (CMV-ERI)' con el CMV, la depreciación y la guía de conciliación
    fiscal — formato 2517 (anexo del 210)."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            escribir_borrador_comerciante(salida, datos, liq)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(
        io.BytesIO(contenido),
        as_attachment=True,
        download_name=f"Borrador_Comerciante_Conciliacion2517_{nit}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.post("/api/informe-comerciante-pdf")
@pro_requerido
def informe_comerciante_pdf():
    """Informe ejecutivo del borrador del comerciante en PDF (marca Tributando):
    actividad comercial, CMV, patrimonio, activos y guía de conciliación 2517."""
    from src.informe_comerciante import generar_informe_comerciante_pdf
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    exogena = _exogena_de(cuerpo.get("token", ""))
    liq = calcular(datos, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            generar_informe_comerciante_pdf(salida, datos, liq, exogena)
        contenido = salida.read_bytes()
    finally:
        salida.unlink(missing_ok=True)

    nit = datos.contribuyente.nit or "sin_nit"
    return send_file(io.BytesIO(contenido), as_attachment=True,
                     download_name=f"Informe_Comerciante_{nit}.pdf",
                     mimetype="application/pdf")


@app.post("/api/documentos-cliente")
@pro_requerido
def documentos_cliente_json():
    """Lista personalizada de documentos que el cliente debe entregar, deducida
    de su exógena, + texto listo para WhatsApp. Solo pases activos."""
    from src import documentos_cliente as _dc
    exogena = _exogena_de((request.get_json(silent=True) or {}).get("token", ""))
    if exogena is None:
        return jsonify({"error": "Sube primero la exógena del cliente."}), 400
    u = usuario_actual()
    contador = (getattr(u, "nombre", "") or "").strip()
    return jsonify({"documentos": _dc.documentos_de(exogena),
                    "whatsapp": _dc.texto_whatsapp(exogena, contador)})


@app.post("/api/documentos-cliente-pdf")
@pro_requerido
def documentos_cliente_pdf():
    """PDF de la lista personalizada, con el nombre del contador."""
    from src import documentos_cliente as _dc
    exogena = _exogena_de((request.get_json(silent=True) or {}).get("token", ""))
    if exogena is None:
        return jsonify({"error": "Sube primero la exógena del cliente."}), 400
    u = usuario_actual()
    contador = (getattr(u, "nombre", "") or "").strip()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        _dc.generar_pdf(salida, exogena, contador)
        nombre = f"Documentos_{exogena.identificacion or 'cliente'}.pdf"
        return send_file(salida, as_attachment=True, download_name=nombre,
                         mimetype="application/pdf")
    finally:
        pass   # el tmp se limpia con el SO; send_file lo necesita abierto


@app.route("/api/mi-logo", methods=["GET", "POST", "DELETE"])
@pro_requerido
def mi_logo():
    """Logo de la firma del contador para el resumen ejecutivo. Se guarda en la
    base (base64) para sobrevivir los despliegues. Máx. 400 KB, PNG o JPG."""
    import base64
    u = usuario_actual()
    if request.method == "GET":
        if not getattr(u, "logo_b64", None):
            return jsonify({"ok": False}), 404
        crudo = base64.b64decode(u.logo_b64)
        tipo = "image/png" if crudo[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        return send_file(io.BytesIO(crudo), mimetype=tipo)
    if request.method == "DELETE":
        u.logo_b64 = None
        db.session.commit()
        return jsonify({"ok": True})
    f = request.files.get("logo")
    if not f:
        return jsonify({"ok": False, "error": "Falta el archivo."}), 400
    crudo = f.read()
    if len(crudo) > 400 * 1024:
        return jsonify({"ok": False, "error": "Máximo 400 KB — reduce la imagen."}), 400
    es_png = crudo[:8] == b"\x89PNG\r\n\x1a\n"
    es_jpg = crudo[:3] == b"\xff\xd8\xff"
    if not (es_png or es_jpg):
        return jsonify({"ok": False, "error": "Solo PNG o JPG."}), 400
    u.logo_b64 = base64.b64encode(crudo).decode()
    db.session.commit()
    return jsonify({"ok": True})


@app.post("/api/resumen-pdf")
@pro_requerido
def resumen_pdf():
    """Genera y descarga el resumen ejecutivo en PDF."""
    cuerpo = request.get_json(silent=True) or {}
    try:
        datos = DatosDeclaracion.from_dict(cuerpo.get("datos", {}))
    except (TypeError, KeyError) as exc:
        return jsonify({"error": f"Datos inválidos: {exc}"}), 400
    exogena = _exogena_de(cuerpo.get("token", ""))
    liq = calcular(datos, PARAMS)
    razones = []
    if exogena is not None:
        topes = exogena.topes_dian or calcular_topes_propios(exogena)
        razones = evaluar_obligacion_declarar(topes, PARAMS)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        u = usuario_actual()
        fl = None
        try:
            nit_dec = (datos.contribuyente.nit or (exogena.identificacion if exogena else ""))
            if nit_dec:
                fl = fecha_limite(nit_dec, PLANTILLA)
        except Exception:
            pass
        liq_base = None
        if exogena is not None:
            try:
                liq_base = calcular(mapear_exogena_a_datos(exogena, PARAMS), PARAMS)
            except Exception:
                liq_base = None
        logo_bytes = None
        if getattr(u, "logo_b64", None):
            import base64
            try:
                logo_bytes = base64.b64decode(u.logo_b64)
            except Exception:
                logo_bytes = None
        generar_resumen_pdf(salida, datos, liq, PARAMS, exogena, razones,
                            preparado_por=(getattr(u, "nombre", "") or "").strip(),
                            fecha_lim=fl,
                            observaciones=str(cuerpo.get("observaciones") or "")[:4000],
                            liq_base=liq_base, logo_bytes=logo_bytes)
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


def _correo_resultado_lead(nombre, limite, obligado, valor):
    """(asunto, html) del correo con el resultado del cálculo gratis."""
    meses = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    fecha_txt = f"{limite.day} de {meses[limite.month]} de {limite.year}" if limite else "por confirmar"
    hola = f"Hola {nombre.split()[0].title()} 👋" if nombre else "Hola 👋"
    linea = ("Según tu exógena, <b>estás obligado a declarar renta</b>."
             if obligado else
             "Con lo reportado quizá no pagues impuesto, pero <b>revisa si estás obligado a presentar</b>.")
    valor_txt = f"${valor:,.0f}".replace(",", ".")
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:540px;margin:auto;color:#2b3242">
      <div style="background:#1e2432;padding:22px 26px;border-radius:14px 14px 0 0;text-align:center">
        <div style="font-size:20px;font-weight:800;color:#fff">Tributando<span style="color:#cdab7e">.co</span></div>
      </div>
      <div style="background:#fff;border:1px solid #e6e2d8;border-top:none;border-radius:0 0 14px 14px;padding:26px">
        <h1 style="font-size:20px;color:#1e2432;margin:0 0 8px">{hola}</h1>
        <p style="font-size:15px;line-height:1.55">{linea}</p>
        <table role="presentation" width="100%" style="margin:14px 0;border-collapse:collapse">
          <tr><td style="padding:10px 0;border-bottom:1px solid #eee;font-size:14px;color:#6a7482">Valor estimado a pagar</td>
              <td style="padding:10px 0;border-bottom:1px solid #eee;text-align:right;font-weight:800;color:#1e2432">{valor_txt}</td></tr>
          <tr><td style="padding:10px 0;font-size:14px;color:#6a7482">Tu fecha límite</td>
              <td style="padding:10px 0;text-align:right;font-weight:800;color:#c8991f">{fecha_txt}</td></tr>
        </table>
        <p style="font-size:14px;line-height:1.55;color:#4b5563">Te recordaremos antes de que venza para que <b>no pagues sanción</b>. Cuando quieras, elaboramos y <b>presentamos tu declaración ante la DIAN</b> por ti.</p>
        <div style="text-align:center;margin:20px 0 6px">
          <a href="https://tributando.co" style="display:inline-block;background:#c8991f;color:#fff;font-weight:800;text-decoration:none;padding:13px 30px;border-radius:11px">Ver mi declaración →</a>
        </div>
        <p style="color:#9aa2b0;font-size:12px;margin-top:14px">Estimado con la información reportada a la DIAN; puede variar. ¿Dudas? Responde este correo.</p>
      </div>
    </div>"""
    return "📄 Tu resultado de renta y tu fecha límite", html


@app.post("/api/mi-resultado")
def guardar_lead_exogena():
    """Guarda el correo de quien calculó gratis (para recordarle y recuperarlo) y
    le envía su resultado. Captura el lead que antes se perdía."""
    b = request.get_json(silent=True) or {}
    token = (b.get("token") or "").strip()
    email = (b.get("email") or "").strip().lower()
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return jsonify({"ok": False, "error": "Escribe un correo válido."}), 400

    carga = _leer_ordenes().get(token) or {}
    nombre = carga.get("nombre", "")
    nit = carga.get("nit", "")
    limite = fecha_limite(nit, PLANTILLA) if nit else None
    try:
        valor = _monto_valido(b.get("valor") or 0)
    except (TypeError, ValueError):
        valor = 0.0
    obligado = bool(b.get("obligado"))

    lead = db.session.get(LeadExogena, email)
    if lead is None:
        lead = LeadExogena(email=email)
        db.session.add(lead)
    lead.nombre = nombre or lead.nombre
    lead.nit = nit or lead.nit
    lead.fecha_limite = limite
    lead.obligado = obligado
    lead.valor = valor
    lead.token = token or lead.token
    lead.origen = lead.origen or _origen_actual() or None
    db.session.commit()

    try:
        from src.correo import enviar_email
        asunto, html = _correo_resultado_lead(nombre, limite, obligado, valor)
        enviar_email(email, asunto, html)
    except Exception:
        app.logger.warning("mi-resultado: no se pudo enviar el correo de resultado")

    # Bono en caliente: al dejar el correo recibe su código de descuento (uno
    # por correo; si ya tiene uno vigente se reutiliza) y queda activo en la
    # sesión para que el checkout lo descuente solo.
    bono_cod = ""
    try:
        from src.auth import Bono
        from src.gerente import BONO_RENTA, _crear_bono
        b = (Bono.query.filter_by(email=email, tipo="renta", usado=False)
             .filter(Bono.expira > datetime.utcnow()).first())
        if b is None:
            b = db.session.get(Bono, _crear_bono(email, "renta"))
        bono_cod = b.codigo
        session["bono"] = bono_cod
    except Exception:
        app.logger.warning("mi-resultado: no se pudo crear el bono")
    return jsonify({"ok": True, "bono": bono_cod,
                    "desc_pdf": 10000, "desc_presentacion": 30000})


@app.post("/api/recalcular-landing")
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
    if "docente_publico" in cuerpo:
        datos.docente_publico = bool(cuerpo["docente_publico"])

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
        "docente_publico": datos.docente_publico,
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
    bono_cod, bono_desc = _canjear_bono(plan)
    ordenes[orden_id] = {
        "tipo": "orden", "token": token, "plan": plan, "origen": _origen_actual(),
        "bono": bono_cod, "descuento": bono_desc,
        "precio": max(PLANES[plan]["precio"] - bono_desc, 0), "contacto": contacto,
        "estado": "pendiente_pago", "fecha": str(date.today()),
        "nit": ordenes[token].get("nit", ""), "nombre": ordenes[token].get("nombre", ""),
    }
    _guardar_ordenes(ordenes)
    return jsonify({"orden_id": orden_id, "plan": PLANES[plan],
                    "precio": ordenes[orden_id]["precio"], "descuento": bono_desc,
                    "pago": PAGO})


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
def reportar_pago():
    """El cliente informa que ya hizo la consignación/transferencia."""
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    # También puede reportar quien venía de un pago rechazado/cancelado en la
    # pasarela y terminó pagando por transferencia o QR.
    if orden["estado"] == "pendiente_pago" or orden["estado"].startswith("pago_"):
        if orden["estado"] != "pago_reportado":
            orden["estado"] = "pago_reportado"
            # Aviso al negocio: hay una consignación por verificar. Nunca tumba
            # el endpoint (la orden queda en el panel /admin de todos modos).
            from src.correo import notificar_pago
            if notificar_pago(orden_id, orden, confirmado=False):
                orden["aviso_pago_enviado"] = True
            try:
                from src.gerente import _telegram_enviar, telegram_configurado
                if telegram_configurado():
                    c = orden.get("contacto") or {}
                    _telegram_enviar(
                        f"💰 Pago por transferencia/QR reportado\n"
                        f"Orden {orden_id} · {orden.get('plan','?')} · "
                        f"${int(orden.get('precio') or 0):,} COP\n"
                        f"{c.get('email','')}\n"
                        f"Verifica en Bancolombia/Nequi y confirma en tributando.co/admin")
            except Exception:
                pass
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

        exogena = _exogena_de(orden.get("token", ""))
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            p_form = Path(tf.name)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tg:
            p_guia = Path(tg.name)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as td:
            p_docs = Path(td.name)
        adj_docs = None
        try:
            generar_formulario_pdf(p_form, datos, liq, PARAMS)
            generar_guia_dian_pdf(p_guia, nombre=nombre,
                                  fecha_limite=str(limite) if limite else None)
            if exogena is not None and exogena.partidas:
                from src import documentos_cliente as _dc
                _dc.generar_pdf(p_docs, exogena)
            else:
                generar_checklist_pdf(p_docs, nombre=nombre,
                                      fecha_limite=str(limite) if limite else None)
            adj_form = p_form.read_bytes()
            adj_guia = p_guia.read_bytes()
            adj_docs = p_docs.read_bytes()
        finally:
            p_form.unlink(missing_ok=True)
            p_guia.unlink(missing_ok=True)
            p_docs.unlink(missing_ok=True)

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
            ("Documentos_que_necesita.pdf", adj_docs, "application/pdf"),
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


def _entregar_licencia_lector(orden_id: str, orden: dict) -> None:
    """Al confirmar el pago del Lector: crea la suscripción y envía la CLAVE DE
    LICENCIA al correo del contador. Idempotente (licencia_lector_enviada)."""
    if orden.get("plan") != "lector" or orden.get("licencia_lector_enviada"):
        return
    email = (orden.get("contacto") or {}).get("email", "").strip()
    plan = orden.get("plan_lector", "independiente")
    if not email:
        return
    try:
        sus = crear_suscripcion(email, plan)   # el período (30/365) sale del plan
        orden["licencia_lector"] = sus.licencia
        # Add-on agente IA: se activa solo si el plan lo incluye (Pro/Max, o el
        # anual Independiente durante la promo). Sin toque manual del admin.
        if plan_incluye_agente(plan):
            try:
                agente_set(sus.licencia, True)
            except Exception:
                pass
        from src.correo import cargar_config_email, enviar_email
        cfg = cargar_config_email()
        if not cfg.get("habilitado"):
            orden["licencia_lector_enviada"] = True   # generada; correo deshabilitado
            return
        info = PLANES_LECTOR.get(plan, {})
        limite = info.get("empresas_max") or 0
        cupo = "empresas ilimitadas" if not limite else f"hasta {limite} empresas"
        nombre = ((orden.get("contacto") or {}).get("nombre", "") or orden.get("nombre", ""))
        primer = nombre.split()[0].title() if nombre else ""
        saludo = f"Hola {primer}," if primer else "Hola,"
        navy, dorado = "#1e2432", "#b8955f"
        sitio = (_CONTACTO.get("sitio") or "https://tributando.co").rstrip("/")
        html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f5f7fa;
          font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1e2b3a">
          <div style="max-width:560px;margin:0 auto;padding:24px">
            <div style="background:#fff;border-radius:16px;overflow:hidden">
              <div style="background:{navy};color:#fff;padding:24px 26px">
                <div style="font-size:1.5rem">🔑</div>
                <div style="font-size:1.2rem;font-weight:800;margin-top:6px">
                  Tu licencia de <span style="color:{dorado}">Tributando Contadores</span> está activa</div>
              </div>
              <div style="padding:24px 26px;font-size:.95rem;line-height:1.65">
                <p>{saludo}</p>
                <p>Confirmamos tu pago del plan <b>{info.get('nombre', plan).upper()}</b>
                   ({cupo}). Esta es tu clave de licencia para el <b>Lector XML</b>:</p>
                <div style="background:#f5f7fa;border:2px dashed {dorado};border-radius:12px;
                  padding:18px;margin:18px 0;text-align:center">
                  <div style="font-family:monospace;font-size:1.4rem;font-weight:800;
                    letter-spacing:1px;color:{navy}">{sus.licencia}</div>
                </div>
                <div style="text-align:center;margin:18px 0">
                  <a href="{DESCARGA_LECTOR_PUBLICA}" style="display:inline-block;background:{navy};
                     color:#fff;text-decoration:none;font-weight:800;padding:14px 28px;border-radius:26px">
                     ⬇ Descargar el programa</a>
                </div>
                <p><b>Cómo usarla:</b> descarga e instala el programa (doble clic al
                   instalador), ábrelo, pega esta clave en el panel «Licencia» y listo.
                   Vence el {sus.vence.isoformat() if sus.vence else '—'}; renovamos con tu próximo pago.</p>
                <p style="color:#6b7280;font-size:.85rem">Orden <code>{orden_id}</code> ·
                   <a href="{sitio}/contadores" style="color:{dorado}">{sitio}/contadores</a></p>
              </div>
            </div>
          </div></body></html>"""
        enviar_email(email, "Tu licencia de Tributando Contadores 🔑", html)
        orden["licencia_lector_enviada"] = True
    except Exception:
        pass


def _finalizar_pago_orden(orden_id: str, orden: dict, ordenes: dict) -> None:
    """Marca la orden como pagada y, si es plan de presentación, conserva la
    exógena y genera el checklist para el trámite. Idempotente."""
    orden["estado"] = ("pagada" if orden["plan"] in ("pdf", "contadores", "lector")
                       else "pagada_en_tramite")

    # Bono canjeado: quemarlo (un solo uso). Idempotente.
    if orden.get("bono"):
        try:
            from src.auth import Bono
            b = db.session.get(Bono, orden["bono"])
            if b is not None and not b.usado:
                b.usado = True
                db.session.commit()
        except Exception:
            db.session.rollback()

    # Suscripción al Lector XML: crea la suscripción y entrega la clave. Idempotente.
    if orden["plan"] == "lector":
        _entregar_licencia_lector(orden_id, orden)

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

    # Push instantáneo a Telegram del dueño (bandera propia: no repetir).
    if not orden.get("aviso_venta_telegram"):
        from src import gerente as _ger
        if _ger.notificar_venta_telegram(orden_id, orden):
            orden["aviso_venta_telegram"] = True

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


@app.post("/api/orden/eliminar")
@autorizado_requerido
def eliminar_orden():
    """Elimina una orden del panel admin y su Excel de exógena asociado.
    Solo personal autorizado. Acción irreversible."""
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    if orden_id not in ordenes:
        return jsonify({"error": "Orden no encontrada."}), 404
    ordenes.pop(orden_id)
    _guardar_ordenes(ordenes)          # el sync borra la fila de la BD
    try:                                # borra el Excel de exógena atado a la orden
        fila = db.session.get(ArchivoExogena, f"orden:{orden_id}")
        if fila is not None:
            db.session.delete(fila)
            db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"ok": True})


@app.post("/api/asesor/atender")
@autorizado_requerido
def atender_asesor():
    """Quita la marca 'pidió asesor' de un usuario (ya se le contactó, o es una
    prueba). Solo personal autorizado, desde /admin."""
    cuerpo = request.get_json(silent=True) or {}
    uid = cuerpo.get("usuario_id")
    u = db.session.get(Usuario, uid) if uid is not None else None
    if u is None:
        return jsonify({"error": "Usuario no encontrado."}), 404
    u.quiere_asesor = False
    db.session.commit()
    return jsonify({"ok": True})


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
    """Devuelve la URL del pago en línea de una orden. Prefiere ePayco (si está
    activo); si no, Wompi. El nombre se conserva por el frontend que ya lo llama."""
    from src import epayco as _epayco_mod
    cuerpo = request.get_json(silent=True) or {}
    orden_id = cuerpo.get("orden_id", "")
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404

    # ePayco activo → usamos el checkout genérico /pagar/<orden_id>.
    if _epayco_mod.activo(EPAYCO):
        return jsonify({"url": f"{_base_url_publica()}/pagar/{orden_id}"})

    if not wompi_mod.activo(WOMPI):
        return jsonify({"error": "El pago en línea no está habilitado."}), 400
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


@app.post("/admin/orden/<orden_id>/reentregar")
@autorizado_requerido
def admin_reentregar(orden_id):
    """Reenvía al cliente su paquete PDF (formulario + documentos + guía)."""
    ordenes = _leer_ordenes()
    orden = ordenes.get(orden_id)
    if not orden or orden.get("tipo") != "orden":
        return jsonify({"error": "Orden no encontrada."}), 404
    orden["entrega_cliente_enviada"] = False           # forzar reenvío
    _entregar_pdf_al_cliente(orden_id, orden, ordenes)
    _guardar_ordenes(ordenes)
    ok = bool(orden.get("entrega_cliente_enviada"))
    return jsonify({"ok": ok, "email": (orden.get("contacto") or {}).get("email", "")})


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
    token = orden.get("token", "")
    carga = ordenes.get(token, {})
    limite = fecha_limite(carga.get("nit", orden.get("nit", "")), PLANTILLA)
    exogena = _exogena_de(token)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        salida = Path(tmp.name)
    try:
        if exogena is not None and exogena.partidas:
            # Checklist PERSONALIZADO: solo los documentos que ESTA exógena pide.
            from src import documentos_cliente as _dc
            _dc.generar_pdf(salida, exogena)
        else:
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
        if nuevo:
            # motivo y WhatsApp que el usuario escribe al pedir el asesor
            motivo = (cuerpo.get("asesor_motivo") or "").strip()[:500]
            whats = (cuerpo.get("asesor_whatsapp") or "").strip()[:30]
            if motivo:
                u.asesor_motivo = motivo
            if whats:
                u.asesor_whatsapp = whats
    db.session.commit()

    aviso_enviado = False
    if pidio_asesor_ahora:
        from src.correo import notificar_solicitud_asesor
        aviso_enviado = notificar_solicitud_asesor(
            nombre=u.nombre or "", email_usuario=u.email or "",
            cedula=u.cedula or "", limite=u.fecha_limite,
            telefono=u.asesor_whatsapp or "", motivo=u.asesor_motivo or "")

    return jsonify({"acepta_recordatorios": u.acepta_recordatorios,
                    "quiere_asesor": u.quiere_asesor,
                    "aviso_enviado": aviso_enviado})


@app.post("/api/pase/desbloquear")
@autorizado_requerido
def desbloquear_pase():
    """Libera el bloqueo de login (códigos fallidos) de un contador con pase."""
    correo = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    if not correo:
        return jsonify({"error": "Falta el correo"}), 400
    u = Usuario.query.filter(db.func.lower(Usuario.email) == correo).first()
    if u is None:
        return jsonify({"error": "No hay usuario con ese correo"}), 404
    limpiar_intentos_fallidos(u)
    return jsonify({"ok": True})


@app.get("/admin")
@autorizado_requerido
def admin():
    """Panel local para verificar consignaciones y gestionar trámites.
    OJO: sin autenticación — solo para uso local. Agregar login antes de
    publicar en internet."""
    ordenes = _leer_ordenes()
    # hora real de cada orden (última actualización = pago/confirmación), en hora de Bogotá
    from zoneinfo import ZoneInfo as _ZI
    _tz_bog = _ZI("America/Bogota")
    horas = {}
    for fila_kv in OrdenRegistro.query.all():
        if fila_kv.actualizado:
            horas[fila_kv.id] = fila_kv.actualizado.replace(
                tzinfo=_ZI("UTC")).astimezone(_tz_bog).strftime("%I:%M %p").lstrip("0").lower()
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
            # candado de login: si el contador se bloqueó por códigos fallidos,
            # mostrarlo aquí con botón para liberarlo sin esperar los 15 min
            correo_pase = (c.get("email") or "").strip().lower()
            u_pase = (Usuario.query.filter(db.func.lower(Usuario.email) == correo_pase).first()
                      if correo_pase else None)
            if u_pase and esta_bloqueado(u_pase):
                restante = int((u_pase.bloqueado_hasta - datetime.utcnow()).total_seconds() // 60) + 1
                acciones += (f"<br><small style='color:#b3372f'>🔒 login bloqueado "
                             f"{restante} min (códigos fallidos)</small> "
                             f"<button onclick=\"desbloquear('{correo_pase}')\" "
                             f"style='background:#b3372f;color:#fff;border:0;border-radius:6px;"
                             f"padding:4px 8px;cursor:pointer;font-size:12px'>🔓 Desbloquear</button>")
        else:
            acciones = (f"<a href='/api/orden/{oid}/formulario.pdf'>F210 PDF</a> · "
                        f"<a href='/api/orden/{oid}/documentos.pdf'>Checklist</a> · "
                        f"<a href='/api/orden/{oid}/exogena.xlsx'>Exógena</a>")
        acciones += (f"<br><button onclick=\"borrarOrden('{oid}')\" "
                     f"style='margin-top:4px;background:none;border:0;color:#b3372f;"
                     f"cursor:pointer;font-size:12px'>🗑 Eliminar</button>")
        filas.append(
            f"<tr><td>{o.get('fecha','')}<br><small style='color:#8a919c'>"
            f"{horas.get(oid, '')}</small></td><td><code>{oid}</code></td>"
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
            wa = (u.asesor_whatsapp or "").strip()
            wa_num = "".join(ch for ch in wa if ch.isdigit())
            if wa_num and not wa_num.startswith("57") and len(wa_num) == 10:
                wa_num = "57" + wa_num
            wa_html = (f"<br>📱 <a href='https://wa.me/{wa_num}' target='_blank' "
                       f"style='color:#1e7d43;font-weight:700'>{wa}</a>" if wa_num else
                       "<br><small style='color:#9db0c4'>sin WhatsApp</small>")
            mot = (u.asesor_motivo or "").strip()
            mot_html = (f"<br><span style='color:#5a6b7f'>💬 {mot}</span>" if mot else
                        "<br><small style='color:#9db0c4'>sin motivo</small>")
            asesor = ("<b style='color:#b3372f'>⚑ PIDIÓ ASESOR</b>"
                      f"{wa_html}{mot_html}<br>"
                      f"<button onclick=\"atender({u.id})\" "
                      f"style='margin-top:4px;background:none;border:0;color:#1e7d43;"
                      f"cursor:pointer;font-size:12px'>✓ Atendido / quitar</button>")
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
    correos_m = set()
    for m in MuestraContador.query.order_by(MuestraContador.creado.desc()).all():
        fecha_m = m.creado.strftime("%Y-%m-%d %H:%M") if m.creado else ""
        correos_m.add((m.email or "").lower())
        filas_m.append(
            f"<tr><td>{fecha_m}</td><td>{m.email or ''}</td>"
            f"<td>{m.nit_muestra or '—'}</td>"
            f"<td><button onclick=\"reiniciar({m.usuario_id})\" "
            f"style='background:#b3372f;color:#fff;border:0;border-radius:6px;"
            f"padding:6px 10px;cursor:pointer'>↺ Reiniciar prueba</button></td></tr>")
    # muestras del flujo por correo + código (sin registro OAuth)
    from src.auth import MuestraContadorEmail as _MCE
    for m in _MCE.query.order_by(_MCE.creado.desc()).all():
        if (m.email or "").lower() in correos_m:
            continue
        fecha_m = m.creado.strftime("%Y-%m-%d %H:%M") if m.creado else ""
        filas_m.append(
            f"<tr><td>{fecha_m}</td><td>{m.email or ''} "
            f"<span style='color:#8a919c;font-size:.75rem'>(por correo)</span></td>"
            f"<td>{m.nit_muestra or '—'}</td>"
            f"<td><button onclick=\"reiniciarEmail('{m.email}')\" "
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

    # ---- resumen (tarjetas) ----
    n_pend = sum(1 for o in ordenes.values() if o.get("tipo") == "orden"
                 and o.get("estado") in ("pendiente_pago", "pago_reportado"))
    try:
        n_susc = SuscripcionLector.query.filter_by(activa=True).count()
    except Exception:
        n_susc = 0
    def _card(valor, etiqueta, ancla, urgente=False):
        bg = "#fff6f5" if urgente and valor else "#fff"
        bd = "#f0c8c4" if urgente and valor else "#dbe3ec"
        col = "#b3372f" if urgente and valor else "#123f6b"
        return (f"<a href='{ancla}' class='card' style='background:{bg};border-color:{bd}'>"
                f"<div class='n' style='color:{col}'>{valor}</div>"
                f"<div class='l'>{etiqueta}</div></a>")
    resumen = (
        _card(n_pend, "💳 Pagos por confirmar", "#ordenes", urgente=True) +
        _card(n_asesor, "⚑ Piden asesor", "#usuarios", urgente=True) +
        _card(len(filas_u), "👥 Usuarios", "#usuarios") +
        _card(n_susc, "🔑 Suscripciones Lector", "/admin/lector") +
        _card(len(filas_a), "👔 Pases activos", "#acceso") +
        _card(len(filas_m), "🧪 Probaron gratis", "#prueba"))

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>Admin — Panel</title>
<style>body{{font-family:-apple-system,sans-serif;margin:24px;color:#1e2b3a}}
table{{border-collapse:collapse;width:100%;font-size:.85rem;margin-bottom:34px}}
th,td{{border-bottom:1px solid #dbe3ec;padding:8px;text-align:left;vertical-align:top}}
th{{background:#123f6b;color:#fff}}
h2{{margin-top:10px;scroll-margin-top:16px;border-top:2px solid #eef2f7;padding-top:18px}}
button{{transition:transform .15s ease, box-shadow .15s ease; cursor:pointer}}
button:hover:not(:disabled){{transform:translateY(-2px); box-shadow:0 8px 18px rgba(10,25,45,.18)}}
.nav-admin{{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 18px}}
.nav-admin a{{display:inline-flex;align-items:center;gap:6px;background:#123f6b;color:#fff;
  text-decoration:none;padding:9px 16px;border-radius:8px;font-size:.9rem;font-weight:600;
  transition:transform .15s ease, box-shadow .15s ease}}
.nav-admin a:hover{{transform:translateY(-2px);box-shadow:0 8px 18px rgba(10,25,45,.18)}}
.nav-admin a.actual{{background:#e8eef5;color:#123f6b;cursor:default;pointer-events:none}}
.resumen{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:0 0 28px}}
.card{{border:1px solid #dbe3ec;border-radius:12px;padding:14px 16px;text-decoration:none;
  display:block;transition:transform .15s ease, box-shadow .15s ease}}
.card:hover{{transform:translateY(-2px);box-shadow:0 8px 18px rgba(10,25,45,.14)}}
.card .n{{font-size:1.7rem;font-weight:800;line-height:1}}
.card .l{{font-size:.8rem;color:#5a6b7d;margin-top:4px}}
</style></head><body>
<div class="nav-admin">
  <a class="actual">🏠 Órdenes y usuarios</a>
  <a href="/admin/dashboard" style="background:#1e2432">📊 Panel del negocio</a>
  <a href="/admin/campana" style="background:#8a4b1e">📣 Campañas</a>
  <a href="/admin/lector">🔑 Suscripciones Lector XML</a>
  <a href="/admin/exogenas">🧾 Leads del cálculo</a>
  <a href="/admin/gestor">📅 Uso del Gestor</a>
  <a href="/vencimientos">📅 Gestor de vencimientos</a>
</div>
<div class="resumen">{resumen}</div>

<h2 id="ordenes">💳 Órdenes — verificación de consignaciones</h2>
<p>Cuenta de recaudo: <b>{cuenta}</b>. Verifique en su app Bancolombia que la
consignación llegó (valor y referencia) antes de confirmar.</p>
<table><tr><th>Fecha</th><th>Orden</th><th>Cliente</th><th>Plan</th><th>Valor</th>
<th>Contacto</th><th>Estado</th><th>Acciones</th></tr>{''.join(filas) or
'<tr><td colspan=8>Sin órdenes todavía.</td></tr>'}</table>

<h2 id="usuarios">👥 Usuarios registrados ({len(filas_u)})</h2>
<p>Personas que ingresaron con Google/Microsoft (o demo) y dejaron sus datos.</p>
{aviso_asesor}
<table><tr><th>Nombre</th><th>Correo</th><th>Cédula/NIT</th><th>Vencimiento</th>
<th>Recordatorios</th><th>Asesor</th></tr>{''.join(filas_u) or
'<tr><td colspan=6>Aún no hay usuarios registrados.</td></tr>'}</table>

<h2 id="prueba">👔 Contadores que probaron gratis ({len(filas_m)})</h2>
<p>Cada uno ya usó su declaración de muestra (termómetro de interés).
"Reiniciar" le devuelve su prueba gratis — útil para demos.</p>
<table><tr><th>Fecha</th><th>Contador</th><th>NIT muestra</th><th>Acción</th></tr>{''.join(filas_m) or
'<tr><td colspan=4>Ningún contador ha probado todavía.</td></tr>'}</table>

<h2 id="acceso">🔑 Acceso al liquidador — contadores con pase ({len(filas_a)})</h2>
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
async function desbloquear(email) {{
  if (!confirm('¿Liberar el bloqueo de login de ' + email + '?')) return;
  const r = await fetch('/api/pase/desbloquear', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{email}})}});
  if (r.ok) location.reload(); else alert('Error desbloqueando');
}}
async function confirmar(oid) {{
  if (!confirm('¿Confirmar que la consignación de la orden ' + oid + ' llegó a la cuenta?')) return;
  const r = await fetch('/api/confirmar-pago', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{orden_id: oid}})}});
  if (r.ok) location.reload(); else alert('Error confirmando');
}}
async function borrarOrden(oid) {{
  if (!confirm('¿Eliminar la orden ' + oid + '? Borra la orden y su Excel de exógena. No se puede deshacer.')) return;
  const r = await fetch('/api/orden/eliminar', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{orden_id: oid}})}});
  if (r.ok) location.reload(); else alert('Error eliminando');
}}
async function atender(uid) {{
  if (!confirm('¿Quitar la marca de "pidió asesor" de este usuario?')) return;
  const r = await fetch('/api/asesor/atender', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{usuario_id: uid}})}});
  if (r.ok) location.reload(); else alert('Error');
}}
async function reiniciar(uid) {{
  if (!confirm('¿Devolverle su prueba gratis a este contador?')) return;
  const r = await fetch('/api/muestra-contador/reset', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{usuario_id: uid}})}});
  if (r.ok) location.reload(); else alert('Error reiniciando');
}}
async function reiniciarEmail(email) {{
  if (!confirm('¿Devolverle la prueba gratis a ' + email + '?')) return;
  const r = await fetch('/api/muestra-contador/reset', {{method:'POST',
    headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{email}})}});
  if (r.ok) location.reload(); else alert('Error reiniciando');
}}
</script></body></html>"""


_VENTAS_CSS = """<style>
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:860px;margin:0 auto;padding:22px;color:#1e2432}
h1{font-size:1.35rem} .tot{font-size:2.1rem;font-weight:800;color:#1f8a5f}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.c{border:1px solid #e2ddd2;border-radius:12px;padding:14px 18px;min-width:160px}
.c .n{font-size:1.6rem;font-weight:800;color:#b8955f} .c .l{font-size:.85rem;color:#5c6470} .c .m{font-weight:700;margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:.9rem;margin-top:8px}
th,td{padding:8px 10px;border-bottom:1px solid #eee;text-align:left} th{background:#1e2432;color:#fff}
input,button{font-size:1rem;padding:7px 12px;border-radius:8px;border:1px solid #ccc} a{color:#b8955f;text-decoration:none}
</style>"""


@app.get("/admin/ventas")
@autorizado_requerido
def admin_ventas():
    """Estadísticas de ventas de un mes: órdenes pagadas, total y desglose."""
    from collections import defaultdict
    hoy = date.today()
    mes = (request.args.get("mes") or hoy.strftime("%Y-%m")).strip()[:7]
    NOMBRE = {"lector": "Lector XML", "contadores": "Pase de temporada",
              "pdf": "Declaración 210", "renta": "Declaración de renta"}
    total = 0.0
    ventas = []
    por_prod = defaultdict(lambda: {"n": 0, "monto": 0.0})
    for fila in OrdenRegistro.query.all():
        try:
            o = json.loads(fila.data)
        except Exception:
            continue
        if o.get("tipo") != "orden" or not str(o.get("estado", "")).startswith("pagada"):
            continue
        f = fila.actualizado.date() if fila.actualizado else None
        if f is None:
            try:
                f = date.fromisoformat((o.get("fecha") or "")[:10])
            except Exception:
                continue
        if f.strftime("%Y-%m") != mes:
            continue
        precio = float(o.get("precio") or o.get("valor") or 0)
        plan = o.get("plan", "otro")
        nom = NOMBRE.get(plan, plan or "otro")
        det = nom + (f" · {o.get('plan_lector','')}" if plan == "lector" and o.get("plan_lector") else "")
        email = (o.get("contacto") or {}).get("email", "") or o.get("email", "")
        ventas.append({"fecha": f.isoformat(), "producto": det, "email": email, "monto": precio})
        total += precio
        por_prod[nom]["n"] += 1
        por_prod[nom]["monto"] += precio
    ventas.sort(key=lambda v: v["fecha"], reverse=True)

    def _p(n):
        return "$" + f"{int(round(n)):,}".replace(",", ".")

    cards = "".join(
        f"<div class='c'><div class='n'>{d['n']}</div><div class='l'>{k}</div>"
        f"<div class='m'>{_p(d['monto'])}</div></div>"
        for k, d in sorted(por_prod.items(), key=lambda x: -x[1]['monto']))
    filas = "".join(
        f"<tr><td>{v['fecha']}</td><td>{v['producto']}</td><td>{v['email']}</td>"
        f"<td style='text-align:right'>{_p(v['monto'])}</td></tr>" for v in ventas)
    if not filas:
        filas = "<tr><td colspan='4' style='color:#888;padding:14px'>Sin ventas pagadas en este mes.</td></tr>"
    cuerpo = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <title>Ventas — {mes}</title><meta name="viewport" content="width=device-width,initial-scale=1">{_VENTAS_CSS}</head>
    <body><p><a href="/admin">← Panel de admin</a></p>
    <h1>📊 Ventas del mes — <b>{mes}</b></h1>
    <form method="get" style="margin:10px 0">Mes: <input type="month" name="mes" value="{mes}"> <button>Ver</button></form>
    <div class="tot">{_p(total)} <span style="font-size:1rem;color:#5c6470;font-weight:400">· {len(ventas)} venta(s) pagada(s)</span></div>
    <div class="cards">{cards}</div>
    <table><tr><th>Fecha</th><th>Producto</th><th>Correo</th><th>Valor</th></tr>{filas}</table>
    <p style="color:#8a919c;font-size:.82rem;margin-top:14px">Cuenta las órdenes en estado «pagada» por su fecha de pago.</p>
    </body></html>"""
    return cuerpo


@app.post("/api/muestra-contador/reset")
@autorizado_requerido
def reset_muestra_contador():
    """Devuelve la prueba gratis a un contador (borra su registro de muestra).
    Solo personal autorizado, desde /admin."""
    cuerpo = request.get_json(silent=True) or {}
    uid = cuerpo.get("usuario_id")
    email = (cuerpo.get("email") or "").strip().lower()
    if uid is not None:
        fila = db.session.get(MuestraContador, uid)
        if fila is None:
            return jsonify({"error": "No encontrado."}), 404
        db.session.delete(fila)
    elif email:
        from src.auth import CodigoMuestra, MuestraContadorEmail
        fila = db.session.get(MuestraContadorEmail, email)
        if fila is None:
            return jsonify({"error": "No encontrado."}), 404
        db.session.delete(fila)
        cod = db.session.get(CodigoMuestra, email)
        if cod is not None:
            db.session.delete(cod)
    else:
        return jsonify({"error": "Falta usuario_id o email."}), 400
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
def politica_cache(resp):
    """Estáticos con caché larga (van versionados con ?v=); lo dinámico sin
    caché. Antes TODO iba con no-store y cada visita re-descargaba hasta el
    video de fondo — por eso el consumo de ancho de banda en Render."""
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
    else:
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ============ Tributando Contadores — Lector XML (candado de suscripción) ============

@app.route("/api/lector/licencia", methods=["POST"])
def api_lector_licencia():
    """El Lector XML local valida su clave de licencia contra el servidor."""
    b = request.get_json(silent=True) or {}
    return jsonify(estado_licencia(b.get("licencia", ""), b.get("equipo", "")))


@app.route("/api/lector/codigo", methods=["POST"])
def api_lector_codigo():
    """Acceso por correo ("como Claude"): el Lector pide un código de 6 dígitos
    para el correo del contador. Si hay suscripción, se envía por email. Por
    seguridad la respuesta es la misma exista o no (no revela correos)."""
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Escribe un correo válido."}), 400
    sus = generar_codigo_lector(email)
    if sus is not None:
        codigo = sus._codigo
        html = (
            "<div style='font-family:sans-serif;max-width:460px;margin:auto'>"
            "<h2 style='color:#1e2432'>Tu código de acceso</h2>"
            "<p>Usa este código para entrar al <b>Lector de tributando.co</b>:</p>"
            f"<p style='font-size:34px;font-weight:700;letter-spacing:6px;"
            f"color:#c8991f;margin:18px 0'>{codigo}</p>"
            "<p style='color:#7b7568;font-size:.9rem'>Vence en 15 minutos. "
            "Si no lo pediste, ignora este correo.</p></div>"
        )
        try:
            from src.correo import enviar_email
            enviar_email(email, "Código de acceso — Lector tributando.co", html)
        except Exception as e:
            app.logger.warning("No se pudo enviar el código del Lector: %s", e)
            return jsonify({"ok": False,
                            "error": "No se pudo enviar el correo. Intenta de nuevo."}), 502
    return jsonify({"ok": True,
                    "mensaje": "Si hay una suscripción con ese correo, te llegó un código."})


@app.route("/api/lector/prueba-gratis", methods=["POST"])
def api_lector_prueba_gratis():
    """Prueba gratis self-serve del Lector: 1 empresa, 30 días, SIN tarjeta.
    Crea la suscripción de prueba para el correo (una por correo) y le envía el
    código de acceso. El contador descarga el .exe y entra con correo + código.
    Si el correo ya tiene suscripción (prueba en curso o plan pago) no re-otorga,
    solo le reenvía un código para entrar."""
    from src.auth import crear_suscripcion, SuscripcionLector
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Escribe un correo válido."}), 400
    existente = SuscripcionLector.query.filter_by(email=email).first()
    nueva = existente is None
    if nueva:
        try:
            crear_suscripcion(email, "prueba", dias=30)
        except Exception as e:
            app.logger.warning("prueba-gratis: no se pudo crear la suscripción: %s", e)
            return jsonify({"ok": False, "error": "No pudimos activar la prueba. Intenta de nuevo."}), 500
    sus = generar_codigo_lector(email)
    if sus is not None:
        codigo = sus._codigo
        html = (
            "<div style='font-family:sans-serif;max-width:460px;margin:auto'>"
            "<h2 style='color:#1e2432'>Tu prueba del Lector está lista 🎉</h2>"
            "<p>Descarga el <b>Lector de tributando.co</b> y entra con tu correo y este código:</p>"
            f"<p style='font-size:34px;font-weight:700;letter-spacing:6px;color:#c8991f;margin:18px 0'>{codigo}</p>"
            "<p style='color:#7b7568;font-size:.9rem'>Tu prueba: <b>1 empresa · 30 días · sin tarjeta</b>. "
            "El código vence en 15 minutos.</p></div>"
        )
        try:
            from src.correo import enviar_email
            enviar_email(email, "Tu prueba del Lector — código de acceso", html)
        except Exception as e:
            app.logger.warning("prueba-gratis: no se pudo enviar el código: %s", e)
            return jsonify({"ok": False, "error": "Activamos la prueba pero no pudimos enviar el correo. Intenta pedir el código de nuevo."}), 502
    msg = ("¡Prueba activada! Te enviamos un código a tu correo. Descarga el Lector y entra con tu correo + ese código."
           if nueva else
           "Ese correo ya tiene acceso. Te reenviamos un código para entrar.")
    return jsonify({"ok": True, "nueva": nueva, "mensaje": msg})


@app.route("/api/lector/demo", methods=["POST"])
def api_lector_demo():
    """Solicitud de demostración del Lector: envía los datos del contador a
    contacto@tributando.co para agendarle la cita, y le confirma por correo."""
    b = request.get_json(silent=True) or {}
    def esc(s):
        return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").strip()
    nombre = esc(b.get("nombre"))
    email = (b.get("email") or "").strip().lower()
    telefono = esc(b.get("telefono"))
    pref = esc(b.get("preferencia"))
    if not nombre or not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Escribe tu nombre y un correo válido."}), 400
    from src.correo import enviar_email
    destino = "contacto@tributando.co"
    html_admin = (
        "<div style='font-family:sans-serif;max-width:480px'>"
        "<h3 style='color:#1e2432'>📅 Nueva solicitud de demostración del Lector</h3>"
        f"<p><b>Nombre:</b> {nombre}<br>"
        f"<b>Correo:</b> {esc(email)}<br>"
        f"<b>Teléfono/WhatsApp:</b> {telefono or '—'}<br>"
        f"<b>Prefiere (día/hora):</b> {pref or '—'}</p>"
        f"<p style='color:#7b7568'>Responde a <b>{esc(email)}</b> para coordinar la cita.</p></div>"
    )
    try:
        enviar_email(destino, f"Demo Lector — {nombre}", html_admin)
    except Exception as e:
        app.logger.warning("demo: no se pudo enviar a contacto@: %s", e)
        return jsonify({"ok": False, "error": "No pudimos enviar la solicitud. Intenta de nuevo o escríbenos por WhatsApp."}), 502
    try:  # confirmación al contador (best-effort)
        enviar_email(email, "Recibimos tu solicitud de demostración — Tributando.co",
                     f"<p>Hola {nombre}, recibimos tu solicitud de demostración del <b>Lector XML DIAN</b>. "
                     "Te contactamos muy pronto desde <b>contacto@tributando.co</b> para coordinar la cita. 🙌</p>")
    except Exception:
        pass
    return jsonify({"ok": True, "mensaje": "¡Solicitud enviada! Te contactamos pronto desde contacto@tributando.co para agendar tu demostración."})


@app.route("/api/lector/entrar", methods=["POST"])
def api_lector_entrar():
    """Valida el código del correo y devuelve la licencia + estado para que el
    Lector la guarde y opere igual que con la clave pegada a mano."""
    b = request.get_json(silent=True) or {}
    res = entrar_con_codigo(b.get("email", ""), b.get("codigo", ""), b.get("equipo", ""))
    return jsonify(res), (200 if res.get("valida") or res.get("ok") else 400)


_IA_CONTADOR = (
    "\n\nMODO CONTADOR: Respondes a un CONTADOR PÚBLICO dentro del programa Lector "
    "de tributando.co. Sé técnico, preciso y breve sobre retención en la fuente, "
    "IVA, exógena, plazos DIAN, UVT y procedimiento tributario colombiano 2026. "
    "Cita el artículo o la norma cuando aplique. NO vendas planes ni hables como si "
    "fuera un cliente persona natural. Si no estás seguro de una tarifa vigente, dilo "
    "y sugiere confirmarla en la DIAN.\n"
    "DATOS VIGENTES 2026 (úsalos como ciertos, NO digas que no están definidos):\n"
    "- UVT 2026 = $52.374.\n"
    "- Retención 2026 con el Decreto 572 de 2025 (rige desde 1 jul 2026, bases más bajas): compras "
    "2,5% declarante / 3,5% no declarante (base ≥10 UVT = $523.740); servicios 4% / 6% (base ≥2 UVT "
    "= $104.748); honorarios y comisiones 11% PJ y PN declarante / 10% no declarante (sin base mínima); "
    "arrendamiento inmuebles 3,5% (≥10 UVT), muebles 4%; transporte de carga 1% (≥2 UVT); rendimientos "
    "financieros 7%; otros ingresos 2,5%/3,5% (≥10 UVT).\n"
    "- Autorretención especial (Decreto 572 de 2025, rige desde 1 jul 2026): tarifas por CIIU entre "
    "0,55% y 4,5% sobre ingresos brutos (comercio ≈1,2%; construcción 3,5%; hidrocarburos/carbón 4,5%).\n"
    "- Formulario 350: honorarios=casilla 54, comisiones=55, servicios=56, rendimientos=57, "
    "arrendamientos=58, compras=61, otros=66; reteIVA=79/82.\n"
    "- Sanción mínima 2026 = 10 UVT = $523.740."
)


_IA_AGENTE = (
    "\n\nMODO AGENTE: Además de responder, puedes EJECUTAR acciones del Lector "
    "mediante herramientas. Reglas:\n"
    "1) Si el contador pide una ACCIÓN que una herramienta cubre, tu mensaje debe ser "
    "EXCLUSIVAMENTE el JSON, sin ninguna explicación ni texto antes o después, sin markdown:\n"
    '   {"accion":"NOMBRE","args":{...}}\n'
    "2) Si falta un dato para la acción (p.ej. el mes o el año), pídelo en texto normal.\n"
    "3) Si es una pregunta de conocimiento (norma, tarifa, plazo), responde normal en texto.\n"
    "4) Cuando el usuario te envíe un mensaje que empieza con 'RESULTADO:', es la salida REAL "
    "de la herramienta. Redacta un resumen claro y breve usando EXACTAMENTE las cifras del "
    "RESULTADO (tarifa, retención, totales, saldos) con separador de miles. NO repitas ni uses "
    "estimaciones propias previas, y si el RESULTADO contradice lo que dijiste antes, MANDA el "
    "RESULTADO. Aclara que es un BORRADOR para revisar. No inventes cifras.\n"
    "Herramientas disponibles:\n"
    "- retencion_mes(anio:int, mes:int, autorret_tarifa:number opcional): borrador del Formulario "
    "350 del mes con los CUFEs leídos. Si el contador quiere incluir la AUTORRETENCIÓN especial "
    "(Decreto 572), pasa autorret_tarifa en % — si te da el CIIU o el sector, usa su tarifa "
    "(0.55 a 4.5 según actividad; comercio≈1.2); si no la sabes, pregúntale la tarifa.\n"
    "- iva_mes(periodicidad:'bimestral'|'cuatrimestral', numero:int, anio:int): borrador del Formulario 300.\n"
    "- revisar_cliente(): qué le falta al cliente activo (vencimientos y pendientes).\n"
    "- calcular_retencion(base:number, concepto:string, declarante:bool): cuánto retener en la fuente.\n"
    "- crear_tarea(titulo:string, fecha:'AAAA-MM-DD'): agrega una tarea o vencimiento al cliente activo "
    "(p.ej. 'Retención en la fuente — Julio', fecha '2026-08-13'). Sin fecha clara, pídela.\n"
    "- cargar_obligaciones(tipo:'natural'|'juridica'|'gran'|'rst'): trae las obligaciones del calendario "
    "DIAN del cliente activo (según su NIT) y las crea como tareas, sin duplicar. Si no sabes si el "
    "cliente es persona natural o jurídica, pregúntalo antes.\n"
    "El cliente/empresa ya está fijo en el Lector; no pidas el NIT."
)


@app.route("/api/lector/ia", methods=["POST"])
def api_lector_ia():
    """Buscador IA del Lector: el contador pregunta (retención, IVA, plazos) y la
    IA responde. Con agente=true además puede devolver acciones (JSON) que el
    Lector ejecuta. Requiere licencia válida (protege la cuota de la API)."""
    if not asistente_ia_activo(IA_CFG):
        return jsonify({"error": "El buscador IA no está disponible."}), 503
    b = request.get_json(silent=True) or {}
    est = estado_licencia(b.get("licencia", ""))
    if not est.get("valida"):
        return jsonify({"error": "Necesitas una licencia activa para usar el buscador IA."}), 402
    if not _chat_permitido(_ip_cliente()):
        return jsonify({"error": "Muchas consultas seguidas. Espera un momento. 🙏"}), 429
    mensajes = b.get("mensajes")
    if not isinstance(mensajes, list) or not mensajes:
        pregunta = (b.get("pregunta") or "").strip()
        if not pregunta:
            return jsonify({"error": "Escribe tu pregunta."}), 400
        mensajes = [{"rol": "user", "texto": pregunta}]
    # Complemento "agente": solo si el contador lo tiene activo y no superó el
    # tope mensual. Si no, cae al buscador normal (responde, pero no ejecuta).
    agente_ok, agente_nota = False, None
    if b.get("agente"):
        ultimo = (mensajes[-1] or {}).get("texto", "") if mensajes else ""
        es_resumen = ultimo.strip().startswith("RESULTADO:")
        if es_resumen:
            agente_ok = True                       # 2ª llamada (resumen): no consume cupo
        else:
            uso = agente_consumir(b.get("licencia", ""))
            if uso.get("permitido"):
                agente_ok = True
            elif not uso.get("activo"):
                agente_nota = "agente_off"         # no tiene el complemento
            elif uso.get("tope_alcanzado"):
                agente_nota = "agente_tope"        # se acabó el cupo del mes
    extra = _IA_CONTADOR + (_IA_AGENTE if agente_ok else "")
    try:
        respuesta = responder_ia(mensajes, IA_CFG, system_extra=extra)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        msg = str(e)
        if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
            return jsonify({"error": "Muchas consultas en este momento. Reintenta en unos segundos. 🙏"}), 429
        app.logger.warning("Fallo del buscador IA del Lector: %s", e)
        return jsonify({"error": "No pude responder ahora. Intenta de nuevo."}), 502
    return jsonify({"respuesta": respuesta, "agente": agente_ok, "nota": agente_nota})


@app.route("/api/lector/version", methods=["GET", "POST"])
def api_lector_version():
    """Última versión del Lector, para que avise si hay una nueva."""
    return jsonify({"version": LECTOR_VERSION_LATEST, "url": DESCARGA_LECTOR_URL,
                    "novedades": LECTOR_NOVEDADES})


@app.route("/api/lector/recordatorio-tareas", methods=["POST"])
def api_lector_recordatorio_tareas():
    """Envía al correo del contador un recordatorio con sus tareas pendientes."""
    b = request.get_json(silent=True) or {}
    est = estado_licencia(b.get("licencia", ""))
    if not est.get("valida"):
        return jsonify({"ok": False, "error": "Necesitas una licencia activa."}), 402
    email = est.get("email") or (SuscripcionLector.query
                                 .filter_by(licencia=b.get("licencia", "")).first() or SuscripcionLector()).email
    if not email:
        return jsonify({"ok": False, "error": "No hay correo asociado a la licencia."}), 400
    tareas = b.get("tareas") or []
    if not tareas:
        return jsonify({"ok": False, "error": "No hay tareas para recordar."}), 400
    filas = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #eee'>{t.get('titulo','')}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:{'#b3372f' if t.get('vencida') else '#1e2432'}'>"
        f"{t.get('fecha','sin fecha')}{' · VENCIDA' if t.get('vencida') else ''}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:#7b7568'>{t.get('empresa','')}</td></tr>"
        for t in tareas)
    html = (
        "<div style='font-family:sans-serif;max-width:560px;margin:auto'>"
        "<h2 style='color:#1e2432'>Tus tareas pendientes</h2>"
        "<p>Recordatorio del <b>Lector de tributando.co</b>:</p>"
        "<table style='border-collapse:collapse;width:100%'>"
        "<tr style='text-align:left;color:#7b7568'><th style='padding:6px 10px'>Tarea</th>"
        "<th style='padding:6px 10px'>Vence</th><th style='padding:6px 10px'>Empresa</th></tr>"
        f"{filas}</table>"
        "<p style='color:#7b7568;font-size:.85rem;margin-top:16px'>Enviado desde tu Lector. "
        "Info de apoyo — verifica los plazos oficiales.</p></div>")
    try:
        from src.correo import enviar_email
        enviar_email(email, "Recordatorio de tareas — Lector tributando.co", html)
    except Exception as e:
        app.logger.warning("No se pudo enviar el recordatorio de tareas: %s", e)
        return jsonify({"ok": False, "error": "No se pudo enviar el correo."}), 502
    return jsonify({"ok": True, "email": email, "n": len(tareas)})


@app.route("/api/lector/vencimientos", methods=["POST"])
def api_lector_vencimientos():
    """Obligaciones DIAN del cliente por NIT (calendario 2026), para llenar las
    Tareas del Lector solas. tipo: natural|juridica|gran|rst."""
    from src.vencimientos import vencimientos_de, SUGERENCIAS
    b = request.get_json(silent=True) or {}
    est = estado_licencia(b.get("licencia", ""))
    if not est.get("valida"):
        return jsonify({"ok": False, "error": "Necesitas una licencia activa."}), 402
    nit = b.get("nit", "")
    if not "".join(c for c in str(nit) if c.isdigit()):
        return jsonify({"ok": False, "error": "Falta el NIT del cliente."}), 400
    tipo = (b.get("tipo") or "juridica").lower()
    obligaciones = b.get("obligaciones") or SUGERENCIAS.get(tipo) or SUGERENCIAS["juridica"]
    eventos = vencimientos_de(nit, obligaciones)
    return jsonify({"ok": True, "tipo": tipo, "vencimientos": [
        {"obligacion": e["obligacion"], "nombre": e["nombre"],
         "etiqueta": e["etiqueta"], "fecha": e["fecha"].isoformat()}
        for e in eventos]})


@app.route("/api/lector/empresa", methods=["POST"])
def api_lector_empresa():
    """Registra una empresa contra la licencia (respeta el límite del plan)."""
    b = request.get_json(silent=True) or {}
    if not b.get("licencia"):
        return jsonify({"ok": False, "error": "Falta la clave de licencia."}), 400
    res = registrar_empresa_lector(b.get("licencia"), b.get("nit"),
                                   b.get("nombre"), b.get("sistema"))
    return jsonify(res), (200 if res.get("ok") else 400)


@app.route("/api/lector/suscripcion", methods=["POST"])
@autorizado_requerido
def api_lector_suscripcion():
    """Admin: crea/renueva la suscripción de un contador y devuelve su licencia."""
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip()
    plan = (b.get("plan") or "independiente").lower()
    if not email or plan not in PLANES_LECTOR:
        return jsonify({"ok": False, "error": "Email o plan inválido."}), 400
    dias = int(b.get("dias") or 30)
    sus = crear_suscripcion(email, plan, dias=dias)
    return jsonify({"ok": True, "licencia": sus.licencia, "plan": sus.plan,
                    "empresas_max": sus.empresas_max,
                    "vence": sus.vence.isoformat() if sus.vence else None})


if __name__ == "__main__":
    import webbrowser
    webbrowser.open("http://127.0.0.1:5210")
    app.run(host="127.0.0.1", port=5210, debug=False)
