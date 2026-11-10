"""Modo mesero para las muestras de restaurantes (10-nov-2026, Taco Tabasco).

El mesero toma el pedido de la mesa desde su celular, la comanda le llega a la
pantalla de cocina (otro celular o un computador) y al final el mesero cierra la
cuenta de la mesa. Como son equipos distintos, las comandas viven en la BD.

Solo para las muestras de la lista MUESTRAS_MESA: es una demostración, se borra
sola a las 36 horas y cada restaurante tiene un tope de comandas abiertas.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, render_template, redirect

from src.auth import db

MUESTRAS_MESA = {
    "taco-tabasco": {"nombre": "Taco Tabasco", "mesas": 15,
                      "meseros": ["Andrés", "Laura", "Camilo", "Valentina"],
                      "plantilla_mesero": "muestras/taco_tabasco_mesero.html",
                      "plantilla_cocina": "muestras/taco_tabasco_cocina.html",
                      "plantilla_caja": "muestras/taco_tabasco_caja.html"},
    # Restaurante de DEMOSTRACIÓN: productos y precios inventados, sin nombre de cliente (video y campaña)
    "restaurante-demo": {"nombre": "Su restaurante", "mesas": 15,
                         "meseros": ["Andrés", "Laura", "Camilo", "Valentina"],
                         "plantilla_mesero": "muestras/restaurante_demo_mesero.html",
                         "plantilla_cocina": "muestras/restaurante_demo_cocina.html",
                         "plantilla_caja": "muestras/restaurante_demo_caja.html"},
}
ESTADOS = ("nueva", "preparando", "lista", "entregada")
TOPE_ABIERTAS = 120          # comandas sin cerrar por muestra
VIDA_HORAS = 36

bp = Blueprint("muestra_mesa", __name__)


class MuestraComanda(db.Model):
    __tablename__ = "muestra_comandas"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), index=True, nullable=False)
    mesa = db.Column(db.Integer, nullable=False)
    mesero = db.Column(db.String(40), default="")
    items = db.Column(db.Text, default="[]")       # [{"n": nombre, "p": precio, "c": cant, "nota": ""}]
    total = db.Column(db.Integer, default=0)
    estado = db.Column(db.String(12), default="nueva")
    cerrada = db.Column(db.Boolean, default=False, index=True)
    pago = db.Column(db.Text, default="")           # json del cierre de la mesa
    origen = db.Column(db.String(12), default="mesa")   # mesa (mesero) · qr (cliente en la mesa) · domicilio · recoge
    cliente = db.Column(db.Text, default="")        # json: nombre, cel, dir, barrio, pago, propina, domicilio
    creada = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    cambio = db.Column(db.DateTime, default=datetime.utcnow)


class MuestraTurno(db.Model):
    """Turno de caja: se abre con una base en efectivo y se cierra contando el efectivo (arqueo)."""
    __tablename__ = "muestra_turnos"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(60), index=True, nullable=False)
    cajero = db.Column(db.String(40), default="")
    base = db.Column(db.Integer, default=0)
    abierto = db.Column(db.DateTime, default=datetime.utcnow)
    cerrado = db.Column(db.DateTime)
    contado = db.Column(db.Integer)
    resumen = db.Column(db.Text, default="")       # json al cerrar


_MIGRADO = {"ok": False}


def _migra():
    """La tabla muestra_comandas ya existía en producción: create_all no le agrega columnas."""
    if _MIGRADO["ok"]:
        return
    try:
        from sqlalchemy import inspect, text
        cols = {c["name"] for c in inspect(db.engine).get_columns("muestra_comandas")}
        with db.engine.begin() as con:
            if "origen" not in cols:
                con.execute(text("ALTER TABLE muestra_comandas ADD COLUMN origen VARCHAR(12) DEFAULT 'mesa'"))
            if "cliente" not in cols:
                con.execute(text("ALTER TABLE muestra_comandas ADD COLUMN cliente TEXT DEFAULT ''"))
        _MIGRADO["ok"] = True
    except Exception:
        # otro worker la agregó al mismo tiempo: se vuelve a mirar en la próxima
        pass


@bp.before_app_request
def _antes():
    if request.path.startswith("/api/muestra/") or request.path.startswith("/muestra/"):
        _migra()


def _cfg(slug):
    return MUESTRAS_MESA.get(slug)


def _purga(slug):
    """Borra lo de más de VIDA_HORAS: la muestra siempre arranca limpia."""
    try:
        lim = datetime.utcnow() - timedelta(hours=VIDA_HORAS)
        MuestraComanda.query.filter(MuestraComanda.slug == slug,
                                    MuestraComanda.creada < lim).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()


def _json(c: MuestraComanda):
    try:
        items = json.loads(c.items or "[]")
    except ValueError:
        items = []
    return {"id": c.id, "mesa": c.mesa, "mesero": c.mesero, "items": items, "total": c.total,
            "estado": c.estado, "cerrada": c.cerrada,
            "creada": _ms(c.creada), "cambio": _ms(c.cambio),
            "origen": c.origen or "mesa", "cliente": _carga(c.cliente)}


def _carga(t):
    try:
        return json.loads(t) if t else {}
    except ValueError:
        return {}


def _ms(d):
    """Las fechas se guardan en UTC sin zona: se marcan UTC para no correrlas 5 horas."""
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000) if d else 0


def _entero(v, tope=5_000_000):
    try:
        return max(0, min(tope, int(float(v or 0))))
    except (TypeError, ValueError):
        return 0


def _limpia_items(raw):
    out = []
    for it in (raw or [])[:60]:
        try:
            n = str(it.get("n", "")).strip()[:80]
            p = int(it.get("p", 0))
            c = int(it.get("c", 1))
        except (TypeError, ValueError, AttributeError):
            continue
        if not n or c < 1 or c > 50 or p < 0 or p > 2_000_000:
            continue
        out.append({"n": n, "p": p, "c": c, "nota": str(it.get("nota", "")).strip()[:120]})
    return out


@bp.get("/muestra/<slug>-mesero")
def pagina_mesero(slug):
    cfg = _cfg(slug)
    if not cfg:
        return redirect("/portafolio")
    resp = render_template(cfg["plantilla_mesero"])
    return resp, 200, {"X-Robots-Tag": "noindex, nofollow"}


@bp.get("/muestra/<slug>-cocina")
def pagina_cocina(slug):
    cfg = _cfg(slug)
    if not cfg:
        return redirect("/portafolio")
    resp = render_template(cfg["plantilla_cocina"])
    return resp, 200, {"X-Robots-Tag": "noindex, nofollow"}


@bp.get("/api/muestra/<slug>/comandas")
def listar(slug):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    _purga(slug)
    q = MuestraComanda.query.filter_by(slug=slug, cerrada=False).order_by(MuestraComanda.id).all()
    cerr = (MuestraComanda.query.filter_by(slug=slug, cerrada=True)
            .order_by(MuestraComanda.cambio.desc()).limit(80).all())
    return jsonify({"ok": True, "abiertas": [_json(c) for c in q],
                    "cerradas": [dict(_json(c), pago=json.loads(c.pago or "{}")) for c in cerr],
                    "ahora": int(time.time() * 1000)})


@bp.post("/api/muestra/<slug>/comanda")
def crear(slug):
    cfg = _cfg(slug)
    if not cfg:
        return jsonify({"ok": False}), 404
    b = request.get_json(silent=True) or {}
    try:
        mesa = int(b.get("mesa", 0))
    except (TypeError, ValueError):
        mesa = 0
    origen = str(b.get("origen") or "mesa")
    if origen not in ("mesa", "qr", "domicilio", "recoge"):
        origen = "mesa"
    if origen in ("domicilio", "recoge"):
        mesa = 0
    elif mesa < 1 or mesa > cfg["mesas"]:
        return jsonify({"ok": False, "error": "Mesa no válida"}), 400
    items = _limpia_items(b.get("items"))
    if not items:
        return jsonify({"ok": False, "error": "La comanda está vacía"}), 400
    if MuestraComanda.query.filter_by(slug=slug, cerrada=False).count() >= TOPE_ABIERTAS:
        return jsonify({"ok": False, "error": "Muchas comandas abiertas: cierre algunas mesas"}), 429
    cli = b.get("cliente") if isinstance(b.get("cliente"), dict) else {}
    cli = {k: str(cli.get(k, ""))[:160] for k in ("nombre", "cel", "dir", "barrio", "pago", "nota")} | \
          {k: _entero(cli.get(k)) for k in ("propina", "domicilio")}
    c = MuestraComanda(slug=slug, mesa=mesa, mesero=str(b.get("mesero", ""))[:40],
                       origen=origen, cliente=json.dumps(cli, ensure_ascii=False),
                       items=json.dumps(items, ensure_ascii=False),
                       total=sum(i["p"] * i["c"] for i in items))
    db.session.add(c)
    db.session.commit()
    return jsonify({"ok": True, "comanda": _json(c)})


@bp.post("/api/muestra/<slug>/comanda/<int:cid>/estado")
def cambiar_estado(slug, cid):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    est = str((request.get_json(silent=True) or {}).get("estado", ""))
    if est not in ESTADOS:
        return jsonify({"ok": False, "error": "Estado no válido"}), 400
    c = MuestraComanda.query.filter_by(slug=slug, id=cid, cerrada=False).first()
    if not c:
        return jsonify({"ok": False, "error": "No existe"}), 404
    c.estado = est
    c.cambio = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "comanda": _json(c)})


@bp.post("/api/muestra/<slug>/mesa/<int:mesa>/cerrar")
def cerrar_mesa(slug, mesa):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    b = request.get_json(silent=True) or {}
    try:
        propina = max(0, min(5_000_000, int(b.get("propina", 0) or 0)))
        personas = max(1, min(30, int(b.get("personas", 1) or 1)))
    except (TypeError, ValueError):
        propina, personas = 0, 1
    pago = {"medio": str(b.get("medio", ""))[:30], "propina": propina, "personas": personas,
            "mesero": str(b.get("mesero", ""))[:40]}
    cs = MuestraComanda.query.filter_by(slug=slug, mesa=mesa, cerrada=False).all()
    if not cs:
        return jsonify({"ok": False, "error": "La mesa no tiene nada abierto"}), 400
    pago["total"] = sum(c.total for c in cs)
    pago["cierre"] = f"m{mesa}-{int(time.time() * 1000)}"   # las rondas de una mesa comparten un solo cierre
    ahora = datetime.utcnow()
    for c in cs:
        c.cerrada = True
        c.pago = json.dumps(pago, ensure_ascii=False)
        c.cambio = ahora
    db.session.commit()
    return jsonify({"ok": True, "pago": pago})


@bp.post("/api/muestra/<slug>/reiniciar")
def reiniciar(slug):
    """Deja la muestra en ceros (botón de la pantalla de cocina, para la demostración)."""
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    MuestraComanda.query.filter_by(slug=slug).delete()
    MuestraTurno.query.filter_by(slug=slug).delete()
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/api/muestra/<slug>/comanda/<int:cid>/cobrar")
def cobrar_pedido(slug, cid):
    """Domicilio o recoge: se cobra pedido por pedido (no hay mesa que cerrar)."""
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    b = request.get_json(silent=True) or {}
    c = MuestraComanda.query.filter_by(slug=slug, id=cid, cerrada=False).first()
    if not c:
        return jsonify({"ok": False, "error": "No existe"}), 404
    cli = _carga(c.cliente)
    pago = {"medio": str(b.get("medio") or cli.get("pago") or "Efectivo")[:30],
            "propina": _entero(b.get("propina", cli.get("propina"))),
            "domicilio": _entero(cli.get("domicilio")), "personas": 1,
            "mesero": str(b.get("cajero", ""))[:40], "total": c.total}
    c.cerrada = True
    c.estado = "entregada"
    c.pago = json.dumps(pago, ensure_ascii=False)
    c.cambio = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "pago": pago})


def _turno_abierto(slug):
    return (MuestraTurno.query.filter_by(slug=slug, cerrado=None)
            .order_by(MuestraTurno.id.desc()).first())


def _resumen(slug, desde=None):
    """Ventas cobradas (sin propina ni domicilio), propinas, por medio, por mesero y por origen."""
    q = MuestraComanda.query.filter_by(slug=slug, cerrada=True)
    if desde:
        q = q.filter(MuestraComanda.cambio >= desde)
    vistos, r = set(), {"ventas": 0, "propinas": 0, "domicilios": 0, "cuentas": 0,
                        "por_medio": {}, "por_mesero": {}, "por_origen": {}, "platos": {}}
    for c in q.all():
        p = _carga(c.pago)
        for it in _carga(c.items) or []:
            r["platos"][it["n"]] = r["platos"].get(it["n"], 0) + it["c"]
        r["por_origen"][c.origen or "mesa"] = r["por_origen"].get(c.origen or "mesa", 0) + c.total
        # una mesa con varias rondas comparte el mismo cierre: la propina y la cuenta se cuentan una vez
        clave = p.get("cierre") or ("p", c.id)
        r["ventas"] += c.total
        medio = p.get("medio") or "Efectivo"
        r["por_medio"][medio] = r["por_medio"].get(medio, 0) + c.total
        quien = p.get("mesero") or c.mesero or "—"
        r["por_mesero"][quien] = r["por_mesero"].get(quien, 0) + c.total
        if clave not in vistos:
            vistos.add(clave)
            r["cuentas"] += 1
            r["propinas"] += p.get("propina", 0) or 0
            r["domicilios"] += p.get("domicilio", 0) or 0
            if p.get("propina"):
                r["por_medio"][medio] += p["propina"]
            if p.get("domicilio"):
                r["por_medio"][medio] += p["domicilio"]
    top = sorted(r["platos"].items(), key=lambda x: -x[1])[:5]
    r["platos"] = [{"n": n, "c": k} for n, k in top]
    r["efectivo"] = r["por_medio"].get("Efectivo", 0)
    return r


def _turno_json(t):
    if not t:
        return None
    return {"id": t.id, "cajero": t.cajero, "base": t.base, "abierto": _ms(t.abierto),
            "cerrado": _ms(t.cerrado) if t.cerrado else 0, "contado": t.contado,
            "resumen": _carga(t.resumen)}


@bp.get("/api/muestra/<slug>/caja")
def caja_estado(slug):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    t = _turno_abierto(slug)
    hist = (MuestraTurno.query.filter(MuestraTurno.slug == slug, MuestraTurno.cerrado.isnot(None))
            .order_by(MuestraTurno.id.desc()).limit(10).all())
    pend = (MuestraComanda.query.filter(MuestraComanda.slug == slug, MuestraComanda.cerrada.is_(False),
                                        MuestraComanda.origen.in_(("domicilio", "recoge")))
            .order_by(MuestraComanda.id).all())
    abiertas = MuestraComanda.query.filter_by(slug=slug, cerrada=False).all()
    mesas = {}
    for c in abiertas:
        if c.mesa:
            m = mesas.setdefault(c.mesa, {"mesa": c.mesa, "total": 0, "mesero": c.mesero, "rondas": 0})
            m["total"] += c.total
            m["rondas"] += 1
    return jsonify({"ok": True, "turno": _turno_json(t),
                    "resumen": _resumen(slug, t.abierto if t else datetime.utcnow() - timedelta(hours=24)),
                    "pendientes": [_json(c) for c in pend],
                    "mesas": sorted(mesas.values(), key=lambda x: x["mesa"]),
                    "historial": [_turno_json(h) for h in hist]})


@bp.post("/api/muestra/<slug>/caja/abrir")
def caja_abrir(slug):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    if _turno_abierto(slug):
        return jsonify({"ok": False, "error": "Ya hay un turno abierto"}), 400
    b = request.get_json(silent=True) or {}
    t = MuestraTurno(slug=slug, cajero=str(b.get("cajero", ""))[:40], base=_entero(b.get("base")))
    db.session.add(t)
    db.session.commit()
    return jsonify({"ok": True, "turno": _turno_json(t)})


@bp.post("/api/muestra/<slug>/caja/cerrar")
def caja_cerrar(slug):
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    t = _turno_abierto(slug)
    if not t:
        return jsonify({"ok": False, "error": "No hay turno abierto"}), 400
    b = request.get_json(silent=True) or {}
    r = _resumen(slug, t.abierto)
    contado = _entero(b.get("contado"), 500_000_000)
    esperado = t.base + r["efectivo"]
    r.update({"esperado": esperado, "diferencia": contado - esperado,
              "abiertas_al_cerrar": MuestraComanda.query.filter_by(slug=slug, cerrada=False).count()})
    t.cerrado = datetime.utcnow()
    t.contado = contado
    t.resumen = json.dumps(r, ensure_ascii=False)
    db.session.commit()
    return jsonify({"ok": True, "turno": _turno_json(t)})


@bp.get("/muestra/<slug>-caja")
def pagina_caja(slug):
    cfg = _cfg(slug)
    if not cfg or not cfg.get("plantilla_caja"):
        return redirect("/portafolio")
    return render_template(cfg["plantilla_caja"]), 200, {"X-Robots-Tag": "noindex, nofollow"}


# --- La carta se instala en el celular como app (PWA) -------------------------------------
_APP = {"taco-tabasco": ("Taco Tabasco", "#e63946"), "restaurante-demo": ("Su Restaurante", "#f2c84b")}


@bp.get("/muestra/<slug>.webmanifest")
def manifiesto(slug):
    if slug not in _APP:
        return redirect("/portafolio")
    nombre, color = _APP[slug]
    m = {"name": nombre, "short_name": nombre[:12], "start_url": f"/muestra/{slug}?app=1",
         "scope": "/muestra/", "display": "standalone", "background_color": "#100d0d",
         "theme_color": "#100d0d", "lang": "es-CO",
         "icons": [{"src": f"/static/muestras/{slug}/icono-192.png", "sizes": "192x192", "type": "image/png"},
                   {"src": f"/static/muestras/{slug}/icono-512.png", "sizes": "512x512", "type": "image/png",
                    "purpose": "any maskable"}]}
    return json.dumps(m, ensure_ascii=False), 200, {"Content-Type": "application/manifest+json"}


_SW = """self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{});"""


@bp.get("/muestra/<slug>-sw.js")
def service_worker(slug):
    if slug not in _APP:
        return "", 404
    return _SW, 200, {"Content-Type": "application/javascript", "Cache-Control": "no-cache",
                      "Service-Worker-Allowed": "/muestra/"}
