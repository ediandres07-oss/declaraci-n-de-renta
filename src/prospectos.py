"""Prospección de empresas nuevas (base del RUES, beneficio a empresarios).

Dos segmentos:
- «empresa»: sociedad recién inscrita con su propio correo → contabilidad en la
  nube, la app y la nómina electrónica.
- «contador»: correo que registra varias empresas o tiene pinta de contador →
  la app para contadores.

Reglas: sale desde contacto@tributando.co (PROSP_SMTP_USER / PROSP_SMTP_PASS),
con tope diario (PROSPECTOS_POR_DIA), un solo correo por prospecto, enlace para
darse de baja en cada correo, y APAGADO hasta que PROSPECTOS_ON=1.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

from src.auth import db

URL = os.environ.get("URL_PUBLICA", "https://tributando.co").rstrip("/")
_RE_CONTADOR = re.compile(r"contab|contad|asesor|tribut|auditor|impuest|finanz|revisor", re.I)
_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]


class Prospecto(db.Model):
    __tablename__ = "prospectos"
    email = db.Column(db.String(200), primary_key=True)
    segmento = db.Column(db.String(12), default="empresa")      # empresa | contador
    razon_social = db.Column(db.String(250), default="")        # contador: lista de empresas
    n_empresas = db.Column(db.Integer, default=1)
    nit = db.Column(db.String(20), default="")
    municipio = db.Column(db.String(80), default="")
    actividad = db.Column(db.String(250), default="")
    fecha_matricula = db.Column(db.String(10), default="")       # AAAA-MM-DD
    origen = db.Column(db.String(60), default="rues")
    estado = db.Column(db.String(12), default="pendiente")      # pendiente | enviado | error | baja
    enviado_en = db.Column(db.DateTime)
    error = db.Column(db.String(300), default="")
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    # CRM: en qué va la conversación comercial. «respondio» y «rebotado» los
    # marca solo la lectura del buzón; lo demás lo pone Edison.
    etapa = db.Column(db.String(20), default="")       # '' | respondio | propuesta | cliente | descartado | rebotado
    nota = db.Column(db.String(500), default="")
    respondio_en = db.Column(db.DateTime)
    # Quién abrió el portafolio: cada correo lleva SU enlace firmado.
    visto_en = db.Column(db.DateTime)
    visitas = db.Column(db.Integer, default=0)
    bienvenida_en = db.Column(db.DateTime)      # saludo al abrir el portafolio (una sola vez)


class BajaCorreo(db.Model):
    """Correos que pidieron no recibir más mensajes comerciales (todas las campañas)."""
    __tablename__ = "bajas_correo"
    email = db.Column(db.String(200), primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    origen = db.Column(db.String(40), default="enlace")


def esta_de_baja(email: str) -> bool:
    return bool(email) and db.session.get(BajaCorreo, email.lower().strip()) is not None


def _secreto() -> bytes:
    return (os.environ.get("SECRET_KEY") or os.environ.get("PASE_SECRET") or "tributando").encode()


def token_baja(email: str) -> str:
    return hmac.new(_secreto(), email.lower().strip().encode(), hashlib.sha256).hexdigest()[:24]


def url_baja(email: str) -> str:
    from urllib.parse import quote
    return f"{URL}/baja?e={quote(email)}&t={token_baja(email)}"


def token_portafolio(email: str) -> str:
    return hmac.new(_secreto(), ("portafolio|" + email.lower().strip()).encode(),
                    hashlib.sha256).hexdigest()[:20]


def url_portafolio(email: str) -> str:
    from urllib.parse import quote
    return f"{URL}/portafolio?e={quote(email)}&t={token_portafolio(email)}"


def registrar_visita(email: str, token: str) -> bool:
    """Anota que ese prospecto abrió el portafolio (enlace firmado; sin firma
    válida no se anota nada: la visita cuenta igual en /admin/visitas)."""
    email = (email or "").lower().strip()
    if not email or not hmac.compare_digest(token or "", token_portafolio(email)):
        return False
    p = db.session.get(Prospecto, email)
    if not p:
        return False
    p.visto_en = datetime.utcnow()
    p.visitas = (p.visitas or 0) + 1
    db.session.commit()
    if not p.bienvenida_en and os.environ.get("PROSP_BIENVENIDA_ON", "").strip() == "1":
        _bienvenida_en_hilo(p.email)
    return True


def plantilla_bienvenida(p: "Prospecto") -> tuple[str, str]:
    """Saludo a quien abre el portafolio: pide los 3 datos para cotizar."""
    nombre = (p.razon_social or "su empresa").strip()
    asunto = f"Gracias por su interés, {nombre}"
    cuerpo = f"""<p>Hola,</p>
<p>Gracias por revisar el portafolio de <b>Tributando</b>. Con gusto le preparamos una propuesta a la medida de <b>{nombre}</b>.</p>
<p>Para cotizarle bien, responda este correo con tres datos:</p>
<ol style="padding-left:18px">
<li>A qué se dedica la empresa.</li>
<li>Cuántas facturas emite y recibe al mes, más o menos.</li>
<li>Cuántos empleados tiene o va a contratar.</li>
</ol>
<p>Si prefiere, escríbame por WhatsApp y lo hablamos.</p>"""
    return asunto, _envolver(cuerpo, p.email, "Escribir por WhatsApp", "https://wa.me/573332470715")


def _bienvenida_en_hilo(email: str) -> None:
    """Manda el saludo sin hacer esperar la página (hilo aparte, una sola vez)."""
    import threading
    from flask import current_app
    app = current_app._get_current_object()

    def _tarea():
        with app.app_context():
            p = db.session.get(Prospecto, email)
            cfg = _config_smtp()
            if not p or p.bienvenida_en or not cfg or esta_de_baja(email):
                return
            from src.correo import enviar_email
            try:
                asunto, html = plantilla_bienvenida(p)
                enviar_email(p.email, asunto, html, cfg)
                p.bienvenida_en = datetime.utcnow()
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()
    threading.Thread(target=_tarea, daemon=True).start()


def dar_de_baja(email: str, token: str) -> bool:
    email = (email or "").lower().strip()
    if not email or not hmac.compare_digest(token or "", token_baja(email)):
        return False
    if not db.session.get(BajaCorreo, email):
        db.session.add(BajaCorreo(email=email))
    p = db.session.get(Prospecto, email)
    if p:
        p.estado = "baja"
    db.session.commit()
    return True


# ---------------------------------------------------------------- carga
def cargar_csv(datos: bytes, origen: str = "rues") -> dict:
    """Carga el CSV del beneficio RUES (separado por «;», latin-1). No duplica:
    un correo que ya existe no se toca. Devuelve el conteo por segmento."""
    for enc in ("utf-8-sig", "latin-1"):
        try:
            txt = datos.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sep = ";" if txt[:600].count(";") > txt[:600].count(",") else ","
    filas = [f for f in csv.DictReader(io.StringIO(txt), delimiter=sep)
             if "@" in (f.get("correo_comercial") or "")
             and "jur" in (f.get("org_juridica") or "").lower()]
    usos = Counter(f["correo_comercial"].strip().lower() for f in filas)
    por_correo = defaultdict(list)
    for f in filas:
        por_correo[f["correo_comercial"].strip().lower()].append(f)
    nuevos = {"empresa": 0, "contador": 0, "ya_estaban": 0}
    for email, fs in por_correo.items():
        if db.session.get(Prospecto, email):
            nuevos["ya_estaban"] += 1
            continue
        contador = usos[email] >= 2 or bool(_RE_CONTADOR.search(email))
        f = max(fs, key=lambda x: x.get("fecha_matricula", ""))
        p = Prospecto(
            email=email, segmento="contador" if contador else "empresa",
            razon_social=(" · ".join(x["razon_social"].strip() for x in fs) if contador
                          else f["razon_social"].strip())[:250],
            n_empresas=len(fs), nit=(f.get("numero_identificacion") or "").lstrip("0")[:20],
            municipio=(f.get("municipio") or "").title()[:80],
            actividad=(f.get("actividad_economica") or "")[:250],
            fecha_matricula=(f.get("fecha_matricula") or "")[:10], origen=origen,
            estado="baja" if esta_de_baja(email) else "pendiente")
        db.session.add(p)
        nuevos[p.segmento] += 1
    db.session.commit()
    return nuevos


# ---------------------------------------------------------------- correos
def _fecha_larga(iso: str) -> str:
    try:
        a, m, d = (int(x) for x in iso.split("-"))
        return f"{d} de {_MESES[m]}"
    except Exception:
        return "estas semanas"


def _envolver(cuerpo: str, email: str, cta_txt: str, cta_url: str) -> str:
    navy, dorado = "#1e2432", "#b8955f"
    return f"""<!DOCTYPE html><html><body style="margin:0;background:#f5f7fa;font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1e2b3a">
<div style="max-width:560px;margin:0 auto;padding:24px"><div style="background:#fff;border-radius:14px;overflow:hidden">
<div style="background:{navy};color:#fff;padding:18px 24px;font-weight:800">Tributando<span style="color:{dorado}">.co</span></div>
<div style="padding:22px 24px;font-size:15px;line-height:1.6">{cuerpo}
<p style="margin:22px 0"><a href="{cta_url}" style="background:{dorado};color:#fff;text-decoration:none;padding:11px 18px;border-radius:8px;font-weight:700">{cta_txt}</a></p>
<p>Un saludo,<br><b>Edison Monsalve</b><br>Contador público · Tributando.co<br>WhatsApp: <a href="https://wa.me/573332470715" style="color:{dorado};font-weight:700">333 247 0715</a></p></div></div>
<p style="font-size:11px;color:#8a94a6;line-height:1.5;padding:12px 8px">Te escribimos porque el correo de tu empresa figura en el Registro Mercantil (RUES), que es público.
Si no quieres recibir más mensajes, <a href="{url_baja(email)}" style="color:#8a94a6">haz clic aquí para darte de baja</a> y no te volveremos a escribir.</p>
</div></body></html>"""


def plantilla(p: Prospecto) -> tuple[str, str]:
    if p.segmento == "contador":
        n = p.n_empresas or 1
        asunto = "Lector XML DIAN: de la DIAN a tu programa contable, sin digitar facturas"
        cuerpo = f"""<p>Hola,</p>
<p>Vi que {'registraste ' + str(n) + ' empresas nuevas' if n > 1 else 'acompañaste el registro de una empresa nueva'} en la Cámara de Comercio estas semanas. Soy contador y armé el <b>Lector XML DIAN</b> para dejar de digitar facturas:</p>
<ul style="padding-left:18px">
<li><b>Descarga las facturas electrónicas</b> de tus clientes desde la DIAN por rango de fechas, con el <b>IVA discriminado</b>.</li>
<li>Aprende las cuentas de cada cliente y arma el <b>plano listo</b> para Siigo, World Office, Helisa o Contai en un clic.</li>
<li>Multi-cliente: cada empresa con su historial.</li>
</ul>
<p><b>1 mes gratis con 1 cliente, sin tarjeta.</b> Pones tu correo, te llega un código y entras.</p>
<p>Y si quieres llevar la <b>contabilidad completa en la nube</b>: la <b>app de Tributando</b> causa sola esas facturas, concilia bancos, saca el IVA, la retención y la exógena, y liquida la <b>nómina electrónica</b>. También con un mes de prueba: <a href="{URL}/contadores/contabilidad" style="color:#b8955f;font-weight:700">ver la app para contadores</a>.</p>"""
        return asunto, _envolver(cuerpo, p.email, "Activar mi prueba del Lector", f"{URL}/contadores/lector")
    nombre = (p.razon_social or "tu empresa").strip()
    asunto = f"{nombre}: lo que la DIAN le pide a una empresa nueva"
    cuerpo = f"""<p>Hola,</p>
<p>Vi que <b>{nombre}</b> se inscribió en la Cámara de Comercio el {_fecha_larga(p.fecha_matricula)}. ¡Felicitaciones! En los primeros meses una empresa nueva debe:</p>
<ul style="padding-left:18px">
<li><b>Habilitar la facturación electrónica</b> ante la DIAN antes de su primera venta.</li>
<li>Si contrata personal: <b>seguridad social y nómina electrónica</b> cada mes.</li>
<li>Presentar las declaraciones que salen en su RUT (IVA, retención en la fuente).</li>
<li><b>Renovar la matrícula</b> antes del 31 de marzo.</li>
</ul>
<p>En <b>Tributando</b> te llevamos la <b>contabilidad en la nube</b>, alimentada sola con tus facturas de la DIAN, la <b>nómina electrónica</b> y los impuestos, con un contador que lo revisa. Responde este correo y te cuento cuánto cuesta para {nombre}.</p>"""
    return asunto, _envolver(cuerpo, p.email, "Ver nuestro portafolio", url_portafolio(p.email))


# ---------------------------------------------------------------- envío
def _config_smtp() -> dict | None:
    from src.correo import cargar_config_email
    user, pwd = os.environ.get("PROSP_SMTP_USER", "").strip(), os.environ.get("PROSP_SMTP_PASS", "").strip()
    if not (user and pwd):
        return None
    cfg = dict(cargar_config_email())
    cfg.update({"user": user, "password": pwd, "remitente": user, "host": "smtp.gmail.com",
                "port": 465, "ssl": True, "remitente_nombre": "Edison · Tributando",
                "responder_a": user})
    return cfg


ETAPAS = (("", "Sin respuesta"), ("respondio", "Respondió"), ("propuesta", "Propuesta enviada"),
          ("cliente", "Cliente"), ("descartado", "Descartado"), ("rebotado", "Rebotó"))
DESDE_IMAP = "28-Sep-2026"          # inicio de la prospección


def _norm(email: str) -> str:
    """Gmail ignora los puntos del usuario: carnicosdelnorte.ge@ = carnicosdelnortege@."""
    email = (email or "").strip().lower()
    u, _, d = email.partition("@")
    if d in ("gmail.com", "googlemail.com"):
        u = u.split("+")[0].replace(".", "")
    return f"{u}@{d}"


def _abrir_smtp(cfg):
    import smtplib
    s = smtplib.SMTP_SSL(cfg.get("host", "smtp.gmail.com"), int(cfg.get("port", 465)), timeout=40)
    s.login(cfg["user"], cfg["password"])
    return s


def enviar_lote(limite: int | None = None) -> int:
    """Manda hasta `limite` correos a prospectos pendientes (los más recientes
    primero) por UNA sola conexión SMTP y con pausa entre correos.

    Antes se abría una conexión e inicio de sesión por correo y Gmail cortaba
    («Connection unexpectedly closed»): 1.438 quedaron como «error» aunque
    cientos sí habían salido. Ahora, si Gmail corta, se reintenta una vez con
    conexión nueva y, si vuelve a cortar, se PARA el lote y el resto queda
    pendiente para mañana; solo un destinatario rechazado queda en error.
    Apagado salvo PROSPECTOS_ON=1. Devuelve cuántos envió."""
    import smtplib
    import time
    if os.environ.get("PROSPECTOS_ON", "").strip() != "1":
        return 0
    cfg = _config_smtp()
    if not cfg:
        return 0
    from src.correo import armar_mensaje
    try:
        revisar_buzon(cfg)          # primero: errores viejos que sí salieron, respuestas y rebotes
    except Exception:  # noqa: BLE001
        db.session.rollback()
    limite = limite or int(os.environ.get("PROSPECTOS_POR_DIA", "150") or 150)
    pausa = float(os.environ.get("PROSPECTOS_PAUSA", "4") or 4)
    enviados, smtp = 0, None
    try:
        for p in (Prospecto.query.filter_by(estado="pendiente")
                  .order_by(Prospecto.fecha_matricula.desc()).limit(limite * 2).all()):
            if enviados >= limite:
                break
            if esta_de_baja(p.email):
                p.estado = "baja"
                db.session.commit()
                continue
            asunto, html = plantilla(p)
            msg = armar_mensaje(p.email, asunto, html, cfg)
            cortar = False
            for intento in (1, 2):
                try:
                    if smtp is None:
                        smtp = _abrir_smtp(cfg)
                    smtp.send_message(msg)
                    p.estado, p.enviado_en, p.error = "enviado", datetime.utcnow(), ""
                    enviados += 1
                    break
                except smtplib.SMTPRecipientsRefused as exc:
                    p.estado, p.error = "error", ("rechazado: " + str(exc))[:300]
                    break
                except (smtplib.SMTPException, OSError) as exc:
                    try:
                        smtp and smtp.close()
                    except Exception:  # noqa: BLE001
                        pass
                    smtp = None
                    if intento == 2:
                        cortar = True
                        p.error = ("corte (queda pendiente): " + str(exc))[:300]
            db.session.commit()
            if cortar:
                break
            time.sleep(pausa)
    finally:
        try:
            smtp and smtp.quit()
        except Exception:  # noqa: BLE001
            pass
    return enviados


# ---------------------------------------------------------------- buzón
def _imap(cfg):
    import imaplib
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    m.login(cfg["user"], cfg["password"])
    return m


def _carpeta(m, atributo: str) -> str | None:
    """Nombre de la carpeta con ese atributo (\\Sent): en Gmail en español es
    «[Gmail]/Enviados»; se busca por el atributo para no depender del idioma."""
    _t, lineas = m.list()
    for ln in lineas or []:
        txt = ln.decode("utf-8", "ignore") if isinstance(ln, bytes) else str(ln)
        if atributo in txt:
            return txt.rsplit(' "/" ', 1)[-1].strip()
    return None


def _cabeceras(m, carpeta: str, campos: str) -> list:
    """Cabeceras pedidas de los mensajes de la carpeta desde DESDE_IMAP."""
    from email.parser import HeaderParser
    m.select(carpeta, readonly=True)
    _t, datos = m.search(None, f"SINCE {DESDE_IMAP}")
    ids = (datos[0] or b"").split()
    out = []
    for k in range(0, len(ids), 400):
        _t, resp = m.fetch(b",".join(ids[k:k + 400]), f"(BODY.PEEK[HEADER.FIELDS ({campos})])")
        for parte in resp or []:
            if isinstance(parte, tuple) and parte[1]:
                out.append(HeaderParser().parsestr(parte[1].decode("utf-8", "ignore")))
    return out


def revisar_buzon(cfg: dict | None = None) -> dict:
    """Lee contacto@ por IMAP y pone al día la prospección:
    - «error» que SÍ aparece en Enviados → enviado (no se le vuelve a escribir);
      el resto de «error» por corte de conexión → pendiente (se reintenta).
    - Respuestas en la bandeja de entrada de un prospecto → etapa «respondio».
    - Rebotes (X-Failed-Recipients) → etapa «rebotado»."""
    from email.utils import getaddresses
    cfg = cfg or _config_smtp()
    if not cfg:
        return {"error": "sin credenciales"}
    # Los que una revisión anterior pasó a pendiente sin haber podido leer
    # Enviados (conservan el mensaje del corte viejo): vuelven a «error» y se
    # concilian de nuevo abajo. Así nunca se le escribe dos veces a nadie.
    for p in Prospecto.query.filter(Prospecto.estado == "pendiente",
                                    Prospecto.error.like("Connection unexpectedly closed%")).all():
        p.estado = "error"
    db.session.commit()
    m = _imap(cfg)
    diag = {}
    try:
        enviados = set()
        sent = _carpeta(m, "\\Sent")
        candidatas = [c for c in (sent, '"[Gmail]/Enviados"', '"[Gmail]/Sent Mail"') if c]
        for c in candidatas:
            try:
                for h in _cabeceras(m, c, "TO"):
                    enviados.update(_norm(a) for _n, a in getaddresses(h.get_all("To", [])))
            except Exception as exc:  # noqa: BLE001
                diag.setdefault("fallos", []).append(f"{c}: {str(exc)[:80]}")
            if enviados:
                diag["carpeta"] = c
                break
        diag["direcciones_en_enviados"] = len(enviados)
        remitentes, fallidos = {}, set()
        for h in _cabeceras(m, "INBOX", "FROM DATE X-FAILED-RECIPIENTS"):
            for a in (h.get("X-Failed-Recipients") or "").split(","):
                if "@" in a:
                    fallidos.add(_norm(a))
            for _n, a in getaddresses(h.get_all("From", [])):
                remitentes.setdefault(_norm(a), h.get("Date", ""))
    finally:
        try:
            m.logout()
        except Exception:  # noqa: BLE001
            pass
    r = {"conciliados": 0, "a_pendiente": 0, "respondieron": 0, "rebotes": 0, **diag}
    # Sin una lectura creíble de Enviados NO se toca ningún «error»: pasarlos
    # a pendiente sería volver a escribirles a cientos que ya recibieron el correo.
    leer_ok = len(enviados) >= 20
    r["enviados_leidos"] = leer_ok
    for p in Prospecto.query.filter(Prospecto.estado.in_(("error", "enviado"))).all():
        n = _norm(p.email)
        if p.estado == "error" and leer_ok:
            if n in enviados:
                p.estado, p.error = "enviado", ""
                r["conciliados"] += 1
            elif "rechazado" not in (p.error or ""):
                p.estado = "pendiente"
                p.error = "no estaba en Enviados: " + (p.error or "")[:200]
                r["a_pendiente"] += 1
        if n in fallidos and p.etapa in ("", None):
            p.etapa = "rebotado"
            r["rebotes"] += 1
        elif n in remitentes and p.etapa in ("", None, "rebotado") and n not in fallidos:
            p.etapa, p.respondio_en = "respondio", datetime.utcnow()
            r["respondieron"] += 1
    db.session.commit()
    return r


def lista_crm(etapa: str | None = None, estado: str | None = None, q: str = "",
              limite: int = 300) -> list:
    qry = Prospecto.query
    if etapa == "vio":
        qry = qry.filter(Prospecto.visto_en.isnot(None))
    elif etapa is not None and etapa != "todas":
        qry = qry.filter(Prospecto.etapa == etapa)
    if estado:
        qry = qry.filter(Prospecto.estado == estado)
    if q:
        like = f"%{q.strip()}%"
        qry = qry.filter(db.or_(Prospecto.razon_social.ilike(like), Prospecto.email.ilike(like),
                                Prospecto.municipio.ilike(like), Prospecto.nit.ilike(like)))
    return qry.order_by(Prospecto.respondio_en.desc().nullslast(),
                        Prospecto.visto_en.desc().nullslast(),
                        Prospecto.fecha_matricula.desc()).limit(limite).all()


def poner_etapa(email: str, etapa: str, nota: str | None = None) -> bool:
    p = db.session.get(Prospecto, (email or "").strip().lower())
    if not p or etapa not in dict(ETAPAS):
        return False
    p.etapa = etapa
    if nota is not None:
        p.nota = nota.strip()[:500]
    db.session.commit()
    return True


def resumen() -> dict:
    q = db.session.query(Prospecto.segmento, Prospecto.estado, db.func.count()).group_by(
        Prospecto.segmento, Prospecto.estado).all()
    out = defaultdict(dict)
    for seg, est, n in q:
        out[seg][est] = n
    # Los errores agrupados por su mensaje (primeros 90 caracteres): sin esto
    # el panel solo decía «error: 1.372» y no había cómo saber si era el cupo
    # diario de Gmail, un correo inválido o una falla de conexión.
    errores = Counter()
    for (e,) in db.session.query(Prospecto.error).filter(Prospecto.estado == "error").all():
        errores[(e or "sin mensaje")[:90]] += 1
    return {"por_segmento": dict(out), "bajas": BajaCorreo.query.count(),
            "errores_top": errores.most_common(8),
            "por_etapa": dict(db.session.query(Prospecto.etapa, db.func.count())
                              .group_by(Prospecto.etapa).all()),
            "vieron_portafolio": Prospecto.query.filter(Prospecto.visto_en.isnot(None)).count(),
            "bienvenidas": Prospecto.query.filter(Prospecto.bienvenida_en.isnot(None)).count(),
            "bienvenida_encendida": os.environ.get("PROSP_BIENVENIDA_ON", "") == "1",
            "encendido": os.environ.get("PROSPECTOS_ON", "") == "1",
            "smtp": bool(_config_smtp()),
            "por_dia": int(os.environ.get("PROSPECTOS_POR_DIA", "150") or 150)}
