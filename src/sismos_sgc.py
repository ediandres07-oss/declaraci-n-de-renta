"""Puente público al feed de sismos del Servicio Geológico Colombiano (SGC).

El SGC publica sus sismos en archive.sgc.gov.co, pero ese bucket solo responde
a pedidos que vienen de www.sgc.gov.co (CORS + un filtro que exige Referer y
User-Agent de navegador). La app «Sismos en Vivo» es un HTML suelto, así que no
puede leerlo directo: este endpoint lo pide del lado del servidor y lo devuelve
como GeoJSON estándar (lon, lat, profundidad; hora en milisegundos), con CORS
abierto y una caché corta para no golpear al SGC.

El feed del SGC trae las coordenadas al revés de GeoJSON: [lat, lon, prof].
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify

sismos_bp = Blueprint("sismos_sgc", __name__)

_FEED = "https://archive.sgc.gov.co/feed/v1.0.1/summary/five_days_all.json"
_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"),
    "Origin": "https://www.sgc.gov.co",
    "Referer": "https://www.sgc.gov.co/",
    "Accept": "application/json, text/plain, */*",
}
_TTL = 60                      # segundos de caché
_cache = {"t": 0.0, "data": None}
_lock = threading.Lock()


def _ms(utc: str) -> int | None:
    """'2026-10-14 00:57' (UTC) → milisegundos epoch."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            d = datetime.strptime((utc or "").strip(), fmt).replace(tzinfo=timezone.utc)
            return int(d.timestamp() * 1000)
        except ValueError:
            continue
    return None


def _normalizar(raw: dict) -> dict:
    feats = []
    for f in (raw or {}).get("features") or []:
        p = f.get("properties") or {}
        co = (f.get("geometry") or {}).get("coordinates") or []
        if len(co) < 2 or co[0] is None or co[1] is None:
            continue
        t = _ms(p.get("utcTime"))
        if not t:
            continue
        fid = str(f.get("id") or "")
        feats.append({
            "type": "Feature",
            "id": "sgc-" + fid,
            "geometry": {"type": "Point",
                         "coordinates": [co[1], co[0], co[2] if len(co) > 2 else None]},
            "properties": {
                "mag": p.get("mag"),
                "magType": p.get("magType"),
                "place": p.get("place") or "",
                "cercanos": p.get("closerTowns") or "",
                "time": t,
                "status": p.get("status") or "",
                "agencia": p.get("agency") or "SGC",
                "felt": p.get("felt") or None,
                "fuente": "SGC",
                "sentido": ("https://sismosentido.sgc.gov.co/EvaluacionIntensidadesServlet?idSismo="
                            + fid + "&metodo=irASismoEspecifico") if fid else "",
                "url": ("https://www.sgc.gov.co/detallesismo/" + fid + "/resumen") if fid else "https://www.sgc.gov.co/sismos",
            },
        })
    feats.sort(key=lambda x: x["properties"]["time"], reverse=True)
    return {"type": "FeatureCollection", "fuente": "Servicio Geológico Colombiano",
            "generado": int(time.time() * 1000), "features": feats}


@sismos_bp.route("/api/sismos-sgc")
def sismos_sgc():
    ahora = time.time()
    with _lock:
        data = _cache["data"] if ahora - _cache["t"] < _TTL else None
    if data is None:
        try:
            r = requests.get(_FEED, headers=_HDRS, timeout=12)
            r.raise_for_status()
            data = _normalizar(r.json())
            with _lock:
                _cache.update(t=ahora, data=data)
        except Exception as e:                                   # noqa: BLE001
            with _lock:
                data = _cache["data"]                            # lo último bueno, si hay
            if data is None:
                resp = jsonify(error=f"SGC no disponible: {str(e)[:120]}", features=[])
                resp.status_code = 502
                resp.headers["Access-Control-Allow-Origin"] = "*"
                return resp
    resp = jsonify(data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp
