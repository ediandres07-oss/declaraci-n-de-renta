"""tributando.co/apps — vitrina de lo que Jarvis construye para Edison.

13-oct-2026 (Edison: «hazlo ya»). Cada app es una carpeta en static/apps/<slug>/
con su index.html; el catálogo vive en static/apps/apps.json. Se publica con
`scripts/publicar_app.sh` (copia, actualiza el catálogo, commit solo de esa
carpeta y push → Render despliega solo).

REGLA: aquí van herramientas sin datos de clientes (sismos, calculadoras,
demos). Todo es público con el enlace; por eso va con noindex, pero NO es
privado.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, redirect, request, send_from_directory

apps_bp = Blueprint("apps_publicas", __name__)

BASE = Path(__file__).resolve().parent.parent / "static" / "apps"


def _catalogo() -> list[dict]:
    try:
        return json.loads((BASE / "apps.json").read_text(encoding="utf-8"))
    except Exception:                                            # noqa: BLE001
        return []


def _noindex(resp):
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@apps_bp.route("/apps")
@apps_bp.route("/apps/")
def apps_inicio():
    tarjetas = "".join(
        f'<a class="t" href="/apps/{html.escape(a["slug"])}/">'
        f'<b>{html.escape(a.get("nombre", a["slug"]))}</b>'
        f'<span>{html.escape(a.get("descripcion", ""))}</span>'
        f'<i>{html.escape(a.get("fecha", ""))}</i></a>'
        for a in sorted(_catalogo(), key=lambda x: x.get("fecha", ""), reverse=True))
    pagina = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>Apps · Tributando</title><style>
body{{margin:0;background:#0b1512;color:#eaf5ef;font-family:-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:40px 16px}}
h1{{font-size:28px;margin:0 0 6px}} h1 span{{color:#37d38f}} p{{color:#9fb5aa;margin:0 0 26px}}
.t{{display:block;background:#11201b;border:1px solid #1f3a30;border-radius:14px;padding:16px 18px;
margin:0 0 12px;color:inherit;text-decoration:none}} .t:hover{{border-color:#37d38f}}
.t b{{display:block;font-size:18px;color:#37d38f}} .t span{{display:block;color:#cfe3d9;margin:4px 0}}
.t i{{font-size:12px;color:#6f8a7e;font-style:normal}}
</style></head><body><main><h1>Apps <span>Tributando</span></h1>
<p>Herramientas hechas por Jarvis para Edison Monsalve.</p>{tarjetas or "<p>Todavía no hay apps.</p>"}
</main></body></html>"""
    return _noindex(Response(pagina, mimetype="text/html"))


@apps_bp.route("/apps/<slug>")
def apps_sin_barra(slug):
    return redirect(f"/apps/{slug}/", code=301)   # con barra, para que sirvan las rutas relativas


@apps_bp.route("/apps/<slug>/")
@apps_bp.route("/apps/<slug>/<path:archivo>")
def apps_archivo(slug, archivo="index.html"):
    carpeta = BASE / slug
    if not slug.replace("-", "").isalnum() or not carpeta.is_dir():
        abort(404)
    if archivo == "index.html":                  # solo la página, no sus recursos (video, imágenes)
        from src.prospectos import registrar_visita_app
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        registrar_visita_app(slug, ip, request.headers.get("User-Agent", ""))
    return _noindex(send_from_directory(carpeta, archivo))


@apps_bp.route("/api/apps/lead", methods=["POST"])
def apps_lead():
    """14-oct: contacto que deja un contador al descargar de una app gratis (exógena Bogotá…).
    Queda en el CRM de prospectos como segmento «contador» con estado «lead»: NO entra a la
    tanda diaria de correos (esa solo toma «pendiente»)."""
    import re
    from datetime import datetime
    from src.auth import db
    from src.prospectos import Prospecto
    d = request.get_json(silent=True) or {}
    correo = (d.get("correo") or "").strip().lower()[:200]
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", correo):
        return jsonify(ok=False, error="correo"), 400
    app_ = re.sub(r"[^a-z0-9-]", "", (d.get("app") or "app").lower())[:30]
    nombre = (d.get("nombre") or "").strip()[:120]
    wa = re.sub(r"[^0-9+ ]", "", d.get("whatsapp") or "")[:20]
    nota = f"{nombre} · WhatsApp {wa} · {(d.get('nota') or '')[:200]} · {datetime.now():%Y-%m-%d %H:%M}"
    p = db.session.get(Prospecto, correo)
    nuevo = p is None
    if p is None:
        p = Prospecto(email=correo, segmento="contador", razon_social=nombre, origen=f"app-{app_}",
                      estado="lead", municipio="Bogotá" if "bogota" in app_ else "", nota=nota[:500])
        db.session.add(p)
    else:
        p.nota = ((p.nota or "") + " | " + nota)[-500:]
    try:
        db.session.commit()
    except Exception:                                            # noqa: BLE001
        db.session.rollback()
        return jsonify(ok=False), 500
    if nuevo:  # 13-nov (Edison: «me avisas quién lo utiliza»): aviso por Telegram y correo
        _avisar_lead(app_, nombre, correo, wa, (d.get("nota") or "")[:200])
    resp = jsonify(ok=True)
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _avisar_lead(app_: str, nombre: str, correo: str, wa: str, nota: str) -> None:
    """Le avisa a Edison (Telegram + correo a ediandres07) cuando alguien nuevo usa una app gratis."""
    import html as _h
    texto = (f"🆕 Nuevo usuario de la app gratis «{app_}»\n{nombre or '—'}\n{correo}\n"
             f"WhatsApp: {wa or '—'}\n{nota}")
    try:
        from src import gerente
        gerente._telegram_enviar(texto)
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.correo import enviar_email
        wa_link = "".join(c for c in wa if c.isdigit())
        if len(wa_link) == 10:
            wa_link = "57" + wa_link
        enviar_email("ediandres07@gmail.com", f"🆕 {nombre or correo} usa la app gratis «{app_}»",
                     "<div style='font-family:Arial,sans-serif;font-size:14px'>"
                     f"<p><b>{_h.escape(nombre or '—')}</b> empezó a usar <b>tributando.co/apps/{_h.escape(app_)}</b>.</p>"
                     f"<p>Correo: {_h.escape(correo)}<br>WhatsApp: {_h.escape(wa or '—')}"
                     + (f" · <a href='https://wa.me/{wa_link}'>escribirle</a>" if wa_link else "")
                     + f"<br>{_h.escape(nota)}</p><p style='color:#777'>Quedó en el CRM de prospectos como «lead».</p></div>")
    except Exception:  # noqa: BLE001
        pass
