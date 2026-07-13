"""Integración con WhatsApp Cloud API (Meta): el asistente de IA responde
mensajes de WhatsApp con el MISMO cerebro (Gemini) que el chat de la web.

La configuración vive en ia.yaml, en un bloque `whatsapp_cloud`:

    whatsapp_cloud:
      habilitado: true
      verify_token: "<texto que TÚ eliges; el mismo que registras en Meta>"
      access_token: "<token permanente de la app de Meta>"
      phone_number_id: "<ID del número en WhatsApp Cloud API>"
      api_version: "v21.0"     # opcional

Falla de forma segura: si falta config, `activo()` devuelve False y el webhook
responde 200 sin intentar usar la API (Meta no reintenta).

Flujo:
  - GET  /api/whatsapp  → handshake de verificación (`verificar_webhook`).
  - POST /api/whatsapp  → mensajes entrantes (`atender`), que por cada mensaje
    nuevo llama al generador de respuesta y la envía de vuelta con `enviar`.

El historial de cada conversación y los ids ya atendidos se guardan EN MEMORIA
del proceso (suficiente para un solo worker; con varios workers cada uno lleva
su propia memoria, lo que solo afecta el contexto de conversaciones largas).
"""
from __future__ import annotations

import logging
import threading
import time

import requests

_log = logging.getLogger(__name__)

_TIMEOUT = 15
_MAX_TURNOS = 10          # turnos de historial que recordamos por remitente
_MAX_REMITENTES = 500     # tope de conversaciones vivas en memoria
_MAX_IDS = 1000           # ids de mensajes ya procesados (anti-duplicado)


def config(cfg: dict | None) -> dict:
    return (cfg or {}).get("whatsapp_cloud", {}) or {}


def activo(cfg: dict | None) -> bool:
    wc = config(cfg)
    return bool(wc.get("habilitado") and wc.get("access_token")
                and wc.get("phone_number_id"))


def verificar_webhook(cfg: dict | None, mode: str, token: str, challenge: str):
    """Handshake GET de Meta. Devuelve `challenge` si el token coincide; si no, None."""
    esperado = config(cfg).get("verify_token", "")
    if mode == "subscribe" and esperado and token == esperado:
        return challenge
    return None


def extraer_mensajes(payload: dict | None) -> list:
    """Devuelve [(remitente, texto, id), ...] de los mensajes de TEXTO del webhook.

    Ignora callbacks de estado (entregado/leído) y tipos que no sean texto.
    """
    fuera = []
    for entry in (payload or {}).get("entry", []):
        for cambio in entry.get("changes", []):
            valor = cambio.get("value", {}) or {}
            for m in valor.get("messages", []) or []:
                if m.get("type") != "text":
                    continue
                remitente = m.get("from", "")
                texto = ((m.get("text") or {}).get("body") or "").strip()
                if remitente and texto:
                    fuera.append((remitente, texto, m.get("id", "")))
    return fuera


# --- memoria de conversación y anti-duplicado (en proceso) ------------------
_hist: dict = {}
_ids_vistos: dict = {}     # id_mensaje -> timestamp
_lock = threading.Lock()


def _recordar_id(msg_id: str) -> bool:
    """True si el id es nuevo; False si ya se procesó (reintento de Meta)."""
    if not msg_id:
        return True
    ahora = time.time()
    with _lock:
        if msg_id in _ids_vistos:
            return False
        _ids_vistos[msg_id] = ahora
        if len(_ids_vistos) > _MAX_IDS:      # descarta la mitad más vieja
            for k, _ in sorted(_ids_vistos.items(),
                               key=lambda kv: kv[1])[:_MAX_IDS // 2]:
                _ids_vistos.pop(k, None)
        return True


def _agregar_turno(remitente: str, rol: str, texto: str) -> list:
    with _lock:
        turnos = _hist.get(remitente, []) + [{"rol": rol, "texto": texto}]
        turnos = turnos[-_MAX_TURNOS:]
        _hist[remitente] = turnos
        if len(_hist) > _MAX_REMITENTES:     # olvida la conversación más vieja
            _hist.pop(next(iter(_hist)), None)
        return list(turnos)


def enviar(cfg: dict | None, destino: str, texto: str) -> bool:
    """Envía un mensaje de texto por WhatsApp Cloud API. True si Meta lo aceptó."""
    wc = config(cfg)
    version = wc.get("api_version", "v21.0")
    url = f"https://graph.facebook.com/{version}/{wc['phone_number_id']}/messages"
    try:
        r = requests.post(
            url,
            json={"messaging_product": "whatsapp", "to": destino,
                  "type": "text", "text": {"body": texto[:4000]}},
            headers={"Authorization": f"Bearer {wc['access_token']}"},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        _log.warning("WhatsApp: error de red enviando a %s: %s", destino, e)
        return False
    if r.status_code >= 400:
        _log.warning("WhatsApp: envío rechazado (%s): %s", r.status_code, r.text[:300])
        return False
    return True


def atender(cfg: dict | None, payload: dict | None, generar_respuesta) -> int:
    """Procesa un webhook entrante y responde cada mensaje nuevo.

    `generar_respuesta(historial)` recibe el historial [{rol, texto}] (con el
    mensaje actual al final) y devuelve el texto de respuesta. Devuelve cuántos
    mensajes se atendieron. Nunca lanza: un fallo con un remitente no frena a los demás.
    """
    atendidos = 0
    for remitente, texto, msg_id in extraer_mensajes(payload):
        if not _recordar_id(msg_id):
            continue
        try:
            historial = _agregar_turno(remitente, "user", texto)
            respuesta = (generar_respuesta(historial) or "").strip()
            if respuesta:
                _agregar_turno(remitente, "assistant", respuesta)
                enviar(cfg, remitente, respuesta)
                atendidos += 1
        except Exception:
            _log.warning("WhatsApp: fallo atendiendo a %s", remitente, exc_info=True)
    return atendidos
