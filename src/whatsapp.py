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


# --- Traspaso a humano: si el contador pide hablar con una persona/el dueño ---
_PAUSA_HORAS = 6                     # tras el traspaso, el bot calla este rato en ese chat
_pausados: dict = {}                # remitente -> timestamp del traspaso
_MSG_HANDOFF = ("¡Con gusto! 🙌 Ya le aviso a *Edison* para que te escriba personalmente. "
                "Dame un momentico. Mientras, cuéntame por aquí lo que necesites.")
_FRASES_HUMANO = (
    "hablar con", "hablar contigo", "con una persona", "con alguien",
    "con un asesor", "con una asesora", "con el dueño", "con el dueno", "con edison",
    "un humano", "atención personal", "atencion personal", "me escribes", "me llamas",
    "me contactas", "me puedes llamar", "me puede llamar", "quiero hablar",
    "puedo hablar", "número de contacto", "numero de contacto",
)


def _pide_humano(texto: str) -> bool:
    t = (texto or "").lower()
    return any(f in t for f in _FRASES_HUMANO)


# --- Escalado por TEMA: dinero, quejas o cancelaciones NO las contesta el bot;
# avisa al dueño y se calla en ese chat (igual que el auto-responder del correo).
_MSG_DELICADO = ("Gracias por escribirnos. 🙏 Este tema lo atiende *Edison* "
                 "directamente; ya le aviso para que te responda personalmente y "
                 "te ayude con esto lo antes posible.")
_FRASES_DELICADAS = (
    # dinero / facturación
    "reembolso", "reembols", "devoluci", "devuélvanme", "devuelvanme",
    "me devuelven", "devolver mi dinero", "me cobraron", "cobro doble",
    "doble cobro", "cobraron de", "cobro indebido", "cobro mal",
    "me facturaron mal", "factura mal", "pago doble", "cobro de más",
    "cobro de mas",
    # quejas / reclamos
    "queja", "reclamo", "reclam", "estoy molesto", "muy molesto",
    "inconforme", "demanda", "demandar", "estafa", "fraude", "pésimo",
    "pesimo",
    # cancelaciones
    "quiero cancelar", "cancelar mi", "cancelar la suscrip", "cancelación de",
    "cancelacion de", "dar de baja",
)


def _tema_delicado(texto: str) -> bool:
    t = (texto or "").lower()
    return any(f in t for f in _FRASES_DELICADAS)


def _pausar(remitente: str) -> None:
    with _lock:
        _pausados[remitente] = time.time()


def _en_pausa(remitente: str) -> bool:
    with _lock:
        t = _pausados.get(remitente)
    return bool(t and (time.time() - t) < _PAUSA_HORAS * 3600)


def atender(cfg: dict | None, payload: dict | None, generar_respuesta,
            on_handoff=None) -> int:
    """Procesa un webhook entrante y responde cada mensaje nuevo.

    `generar_respuesta(historial)` recibe el historial [{rol, texto}] (con el
    mensaje actual al final) y devuelve el texto de respuesta. `on_handoff(remitente,
    texto)` (opcional) se llama cuando el contador pide hablar con una persona: el
    bot avisa al dueño y se calla en ese chat para que él responda. Devuelve cuántos
    mensajes atendió. Nunca lanza: un fallo con un remitente no frena a los demás.
    """
    atendidos = 0
    for remitente, texto, msg_id in extraer_mensajes(payload):
        if not _recordar_id(msg_id):
            continue
        try:
            # El dueño ya está atendiendo este chat: guarda el contexto, no respondas.
            if _en_pausa(remitente):
                _agregar_turno(remitente, "user", texto)
                continue
            # ¿Pide hablar con una persona, o es un TEMA DELICADO (dinero, quejas,
            # cancelaciones)? → no lo contesta el bot: avisa al dueño y se calla.
            humano, delicado = _pide_humano(texto), _tema_delicado(texto)
            if humano or delicado:
                _agregar_turno(remitente, "user", texto)
                aviso = _MSG_HANDOFF if humano else _MSG_DELICADO
                enviar(cfg, remitente, aviso)
                _agregar_turno(remitente, "assistant", aviso)
                _pausar(remitente)
                if on_handoff:
                    try:
                        on_handoff(remitente, texto)
                    except Exception:
                        _log.warning("WhatsApp: fallo avisando el traspaso", exc_info=True)
                atendidos += 1
                continue
            historial = _agregar_turno(remitente, "user", texto)
            respuesta = (generar_respuesta(historial) or "").strip()
            if respuesta:
                _agregar_turno(remitente, "assistant", respuesta)
                enviar(cfg, remitente, respuesta)
                atendidos += 1
        except Exception:
            _log.warning("WhatsApp: fallo atendiendo a %s", remitente, exc_info=True)
    return atendidos
