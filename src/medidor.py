"""Medidor de visitas propio y liviano para la portada y el recorrido 3D (12-nov-2026).

Sin cookies y sin datos personales:
- No se guarda la IP ni el navegador. Para contar personas distintas se guarda un hash
  que cambia cada día (sha256 de día + secreto + IP + navegador, recortado). Con él no
  se puede saber quién es ni seguir a nadie de un día a otro.
- Del referrer solo se guarda el dominio (sin la ruta ni los parámetros).

Eventos que manda el navegador (static/js/medidor.js, con sendBeacon):
- vista: abrió la página (con origen, utm_* y celular/computador).
- paso:  llegó al paso 01–06 del recorrido.
- clic:  ver3d, whatsapp, ar, lector_win, lector_mac, app.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from src.auth import db

PAGINAS = {"/": "portada", "/mundo": "portada", "/contadores/recorrido": "recorrido",
           "/recorrido": "recorrido"}
CLICS = {"ver3d", "whatsapp", "ar", "lector_win", "lector_mac", "app"}
_BOTS = ("bot", "crawler", "spider", "preview", "facebookexternalhit", "whatsapp/", "headless",
         "lighthouse", "pingdom", "uptime")
_SAL = os.environ.get("MEDIDOR_SAL") or os.environ.get("SECRET_KEY") or "tributando-medidor"


class EventoWeb(db.Model):
    """Un evento de la portada o del recorrido. Sin IP ni cookie."""
    __tablename__ = "eventos_web"
    id = db.Column(db.Integer, primary_key=True)
    dia = db.Column(db.Date, index=True)
    hora = db.Column(db.DateTime, default=datetime.utcnow)
    pagina = db.Column(db.String(20))          # portada | recorrido
    tipo = db.Column(db.String(10), index=True)  # vista | paso | clic
    dato = db.Column(db.String(20))            # paso 1..6 o nombre del clic
    origen = db.Column(db.String(30))          # google, instagram, correo, whatsapp, directo…
    ref_dominio = db.Column(db.String(80))
    utm_source = db.Column(db.String(40))
    utm_medium = db.Column(db.String(40))
    utm_campaign = db.Column(db.String(60))
    dispositivo = db.Column(db.String(10))     # celular | computador
    unico = db.Column(db.String(16), index=True)  # hash del día (no identifica a nadie)


def _origen(ref_host: str, utm_source: str) -> str:
    u, r = utm_source.lower(), ref_host.lower()
    for clave, nombre in (("chatgpt", "ia_chatgpt"), ("openai", "ia_chatgpt"), ("perplexity", "ia_perplexity"),
                          ("gemini", "ia_gemini"), ("claude", "ia_claude"), ("copilot", "ia_copilot")):
        if clave in u or clave in r:
            return nombre
    if u:
        if u in ("ig", "instagram", "fb", "facebook", "meta"):
            return "instagram"
        if u in ("wa", "whatsapp"):
            return "whatsapp"
        if u in ("email", "correo", "mail", "campana", "campaña"):
            return "correo"
        return re.sub(r"[^a-z0-9_.-]", "", u)[:30] or "utm"
    if not r:
        return "directo"
    if r.endswith("tributando.co"):
        return "interno"
    if "google." in r and not r.startswith("mail."):
        return "google"
    if any(k in r for k in ("instagram", "facebook", "fb.com", "l.facebook")):
        return "instagram"
    if "whatsapp" in r or r == "wa.me":
        return "whatsapp"
    if any(k in r for k in ("mail.", "outlook", "live.com", "yahoo")):
        return "correo"
    if "youtube" in r or "youtu.be" in r:
        return "youtube"
    if "linkedin" in r or r == "lnkd.in":
        return "linkedin"
    if "bing." in r or "duckduckgo" in r:
        return "buscador"
    return r[:30]


def _unico(ip: str, ua: str, dia: date) -> str:
    return hashlib.sha256(f"{dia.isoformat()}|{_SAL}|{ip}|{ua}".encode()).hexdigest()[:16]


def registrar(datos: dict, ip: str, ua: str) -> bool:
    """Guarda un evento. Devuelve False (sin guardar) si es un bot o viene mal formado."""
    ual = (ua or "").lower()
    if not ual or any(b in ual for b in _BOTS):
        return False
    ruta = (str(datos.get("r") or "/").split("?")[0].split("#")[0].rstrip("/") or "/")
    pagina = PAGINAS.get(ruta)
    tipo = str(datos.get("e") or "")
    if not pagina or tipo not in ("vista", "paso", "clic"):
        return False
    dato = ""
    if tipo == "paso":
        try:
            n = int(datos.get("n"))
        except (TypeError, ValueError):
            return False
        if not 1 <= n <= 6:
            return False
        dato = str(n)
    elif tipo == "clic":
        dato = str(datos.get("b") or "")
        if dato not in CLICS:
            return False
    ref_host = (urlparse(str(datos.get("ref") or "")).hostname or "")[:80].lower()
    utm = datos.get("utm") if isinstance(datos.get("utm"), dict) else {}
    us = str(utm.get("source") or "")[:40]
    disp = "celular" if datos.get("d") == "cel" or re.search(r"mobi|android|iphone|ipad", ual) else "computador"
    hoy = date.today()
    db.session.add(EventoWeb(
        dia=hoy, pagina=pagina, tipo=tipo, dato=dato,
        origen=_origen(ref_host, us), ref_dominio=ref_host or None,
        utm_source=us or None, utm_medium=str(utm.get("medium") or "")[:40] or None,
        utm_campaign=str(utm.get("campaign") or "")[:60] or None,
        dispositivo=disp, unico=_unico(ip or "", ual, hoy)))
    db.session.commit()
    return True


def resumen(dias: int = 30) -> dict:
    """Números para /admin/visitas: por día, por origen, embudo y dispositivo."""
    from sqlalchemy import distinct, func
    desde = date.today() - timedelta(days=dias - 1)
    E = EventoWeb
    base = db.session.query(E).filter(E.dia >= desde)

    def unicos(*filtros):
        # personas distintas = pares (día, hash) distintos
        q = db.session.query(E.dia, E.unico).filter(E.dia >= desde, *filtros).distinct()
        return q.count()

    por_dia = []
    filas = (db.session.query(E.dia, E.pagina, func.count(E.id), func.count(distinct(E.unico)))
             .filter(E.dia >= desde, E.tipo == "vista").group_by(E.dia, E.pagina).all())
    tabla = {}
    for d, pag, vistas, personas in filas:
        t = tabla.setdefault(d, {"dia": d, "portada": 0, "recorrido": 0, "personas": 0})
        t[pag] = vistas
    for d, n in (db.session.query(E.dia, func.count(distinct(E.unico)))
                 .filter(E.dia >= desde, E.tipo == "vista").group_by(E.dia).all()):
        tabla.setdefault(d, {"dia": d, "portada": 0, "recorrido": 0, "personas": 0})["personas"] = n
    wa = dict(db.session.query(E.dia, func.count(E.id))
              .filter(E.dia >= desde, E.tipo == "clic", E.dato == "whatsapp").group_by(E.dia).all())
    for i in range(dias):
        d = date.today() - timedelta(days=i)
        t = tabla.get(d, {"dia": d, "portada": 0, "recorrido": 0, "personas": 0})
        t["whatsapp"] = wa.get(d, 0)
        por_dia.append(t)

    # Origen de cada persona-día = el de su primera vista que no sea navegación interna;
    # así el clic de WhatsApp (que no trae referrer) se atribuye a la campaña por la que entró.
    org_de = {}
    for d, u, org in (db.session.query(E.dia, E.unico, E.origen)
                      .filter(E.dia >= desde, E.tipo == "vista").order_by(E.hora).all()):
        if org != "interno":
            org_de.setdefault((d, u), org)
        else:
            org_de.setdefault((d, u), None)
    org_de = {k: (v or "interno") for k, v in org_de.items()}
    wa_personas = {(d, u) for d, u in db.session.query(E.dia, E.unico)
                   .filter(E.dia >= desde, E.tipo == "clic", E.dato == "whatsapp").distinct().all()}
    vistas_org = dict(db.session.query(E.origen, func.count(E.id))
                      .filter(E.dia >= desde, E.tipo == "vista").group_by(E.origen).all())
    acum = {}
    for k, org in org_de.items():
        a = acum.setdefault(org, {"origen": org, "vistas": vistas_org.get(org, 0), "personas": 0, "whatsapp": 0})
        a["personas"] += 1
        a["whatsapp"] += 1 if k in wa_personas else 0
    por_origen = sorted(acum.values(), key=lambda a: (-a["personas"], -a["vistas"]))

    campanas = (db.session.query(E.utm_source, E.utm_campaign, func.count(E.id))
                .filter(E.dia >= desde, E.tipo == "vista", E.utm_campaign.isnot(None))
                .group_by(E.utm_source, E.utm_campaign).order_by(func.count(E.id).desc()).limit(15).all())

    embudo = [
        ("Abrieron la portada", unicos(E.tipo == "vista", E.pagina == "portada")),
        ("Abrieron el recorrido 3D", unicos(E.tipo == "vista", E.pagina == "recorrido")),
        ("Llegaron al paso 06 (retención 350)", unicos(E.tipo == "paso", E.dato == "6")),
        ("Tocaron WhatsApp", unicos(E.tipo == "clic", E.dato == "whatsapp")),
    ]
    tope = max([n for _, n in embudo] + [1])

    pasos = [(n, unicos(E.tipo == "paso", E.dato == str(n))) for n in range(1, 7)]

    clics = [(b, n) for b, n in (db.session.query(E.dato, func.count(E.id))
                                 .filter(E.dia >= desde, E.tipo == "clic").group_by(E.dato)
                                 .order_by(func.count(E.id).desc()).all())]

    disp = {k: unicos(E.tipo == "vista", E.dispositivo == k) for k in ("celular", "computador")}

    hoy = date.today()
    return {"desde": desde, "dias": dias, "por_dia": por_dia, "por_origen": por_origen,
            "campanas": campanas, "embudo": embudo, "tope": tope, "pasos": pasos, "clics": clics,
            "disp": disp,
            "hoy_personas": db.session.query(E.unico).filter(E.dia == hoy, E.tipo == "vista").distinct().count(),
            "total_personas": unicos(E.tipo == "vista"),
            "total_vistas": base.filter(E.tipo == "vista").count()}


def purgar(dias: int = 400) -> int:
    """Borra eventos de más de `dias` días (la tabla no crece sin fin)."""
    n = EventoWeb.query.filter(EventoWeb.dia < date.today() - timedelta(days=dias)).delete()
    db.session.commit()
    return n
