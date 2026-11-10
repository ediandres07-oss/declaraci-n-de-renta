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
                      "plantilla_cocina": "muestras/taco_tabasco_cocina.html"},
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
    creada = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    cambio = db.Column(db.DateTime, default=datetime.utcnow)


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
            "creada": _ms(c.creada), "cambio": _ms(c.cambio)}


def _ms(d):
    """Las fechas se guardan en UTC sin zona: se marcan UTC para no correrlas 5 horas."""
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000) if d else 0


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
    if mesa < 1 or mesa > cfg["mesas"]:
        return jsonify({"ok": False, "error": "Mesa no válida"}), 400
    items = _limpia_items(b.get("items"))
    if not items:
        return jsonify({"ok": False, "error": "La comanda está vacía"}), 400
    if MuestraComanda.query.filter_by(slug=slug, cerrada=False).count() >= TOPE_ABIERTAS:
        return jsonify({"ok": False, "error": "Muchas comandas abiertas: cierre algunas mesas"}), 429
    c = MuestraComanda(slug=slug, mesa=mesa, mesero=str(b.get("mesero", ""))[:40],
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
    for c in cs:
        c.cerrada = True
        c.pago = json.dumps(pago, ensure_ascii=False)
        c.cambio = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True, "pago": pago})


@bp.post("/api/muestra/<slug>/reiniciar")
def reiniciar(slug):
    """Deja la muestra en ceros (botón de la pantalla de cocina, para la demostración)."""
    if not _cfg(slug):
        return jsonify({"ok": False}), 404
    MuestraComanda.query.filter_by(slug=slug).delete()
    db.session.commit()
    return jsonify({"ok": True})
