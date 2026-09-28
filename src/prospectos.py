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
<p>Un saludo,<br><b>Edison Monsalve</b><br>Contador público · Tributando.co</p></div></div>
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
<p><b>1 mes gratis con 1 cliente, sin tarjeta.</b> Pones tu correo, te llega un código y entras.</p>"""
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
    return asunto, _envolver(cuerpo, p.email, "Ver cómo funciona", f"{URL}/contabilidad")


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


def enviar_lote(limite: int | None = None) -> int:
    """Manda hasta `limite` correos a prospectos pendientes (los más recientes
    primero). Apagado salvo PROSPECTOS_ON=1. Devuelve cuántos envió."""
    if os.environ.get("PROSPECTOS_ON", "").strip() != "1":
        return 0
    cfg = _config_smtp()
    if not cfg:
        return 0
    from src.correo import enviar_email
    limite = limite or int(os.environ.get("PROSPECTOS_POR_DIA", "150") or 150)
    enviados = 0
    for p in (Prospecto.query.filter_by(estado="pendiente")
              .order_by(Prospecto.fecha_matricula.desc()).limit(limite * 2).all()):
        if enviados >= limite:
            break
        if esta_de_baja(p.email):
            p.estado = "baja"
            continue
        asunto, html = plantilla(p)
        try:
            enviar_email(p.email, asunto, html, cfg)
            p.estado, p.enviado_en, p.error = "enviado", datetime.utcnow(), ""
            enviados += 1
        except Exception as exc:  # noqa: BLE001
            p.estado, p.error = "error", str(exc)[:300]
        db.session.commit()
    return enviados


def resumen() -> dict:
    q = db.session.query(Prospecto.segmento, Prospecto.estado, db.func.count()).group_by(
        Prospecto.segmento, Prospecto.estado).all()
    out = defaultdict(dict)
    for seg, est, n in q:
        out[seg][est] = n
    return {"por_segmento": dict(out), "bajas": BajaCorreo.query.count(),
            "encendido": os.environ.get("PROSPECTOS_ON", "") == "1",
            "smtp": bool(_config_smtp()),
            "por_dia": int(os.environ.get("PROSPECTOS_POR_DIA", "150") or 150)}
