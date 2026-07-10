"""Integración con Wompi (pasarela de pagos de Bancolombia) — Web Checkout.

Flujo:
1. El cliente elige pagar → construimos la URL de Web Checkout de Wompi con una
   firma de integridad y lo redirigimos a la página segura de Wompi.
2. Paga con tarjeta / PSE / Nequi en Wompi.
3. Wompi lo devuelve a nuestra 'redirect_url' con el id de la transacción.
4. Consultamos el estado real de esa transacción en la API de Wompi y, si está
   APROBADA, marcamos la orden como pagada. (El webhook hace lo mismo de respaldo.)

Falla de forma segura: si Wompi no está habilitado, la landing solo ofrece la
consignación manual.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlencode

import requests
import yaml

BASE = Path(__file__).resolve().parent.parent
_WOMPI_PATH = BASE / "config" / "wompi.yaml"

_CHECKOUT_URL = "https://checkout.wompi.co/p/"
_API_PROD = "https://production.wompi.co/v1"
_API_TEST = "https://sandbox.wompi.co/v1"


def cargar_config() -> dict:
    if _WOMPI_PATH.exists():
        with open(_WOMPI_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def activo(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else cargar_config()
    return bool(cfg.get("habilitado") and cfg.get("public_key") and cfg.get("integrity_secret"))


def _es_test(cfg: dict) -> bool:
    # Deriva del prefijo de la llave pública (pub_test_ / pub_prod_) o del flag.
    pk = str(cfg.get("public_key", ""))
    if pk.startswith("pub_test_"):
        return True
    if pk.startswith("pub_prod_"):
        return False
    return bool(cfg.get("test", True))


def _api_base(cfg: dict) -> str:
    return cfg.get("api_base") or (_API_TEST if _es_test(cfg) else _API_PROD)


def firma_integridad(referencia: str, monto_centavos: int, moneda: str, secret: str) -> str:
    """SHA256(referencia + monto_en_centavos + moneda + integrity_secret)."""
    cadena = f"{referencia}{monto_centavos}{moneda}{secret}"
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest()


def url_checkout(cfg: dict, referencia: str, monto_centavos: int, redirect_url: str,
                 email: str = "") -> str:
    """Construye la URL de Web Checkout de Wompi con la firma de integridad."""
    moneda = cfg.get("moneda", "COP")
    params = {
        "public-key": cfg["public_key"],
        "currency": moneda,
        "amount-in-cents": monto_centavos,
        "reference": referencia,
        "signature:integrity": firma_integridad(
            referencia, monto_centavos, moneda, cfg["integrity_secret"]),
        "redirect-url": redirect_url,
    }
    if email:
        params["customer-data:email"] = email
    return _CHECKOUT_URL + "?" + urlencode(params)


def consultar_transaccion(cfg: dict, transaction_id: str) -> dict:
    """Consulta el estado real de una transacción en la API de Wompi.

    Devuelve el objeto 'data' (con 'status', 'reference', 'amount_in_cents', ...)
    o {} si falla. No lanza excepción.
    """
    try:
        url = f"{_api_base(cfg)}/transactions/{transaction_id}"
        resp = requests.get(url, timeout=12)
        if resp.status_code == 200:
            return resp.json().get("data", {}) or {}
    except Exception:
        pass
    return {}


def validar_firma_evento(cfg: dict, evento: dict) -> bool:
    """Valida la firma del webhook de Wompi (events_secret).

    checksum = SHA256(valores de 'signature.properties' en orden + timestamp + events_secret)
    """
    secret = cfg.get("events_secret", "")
    if not secret:
        return True  # sin secret configurado, no bloqueamos (se recomienda ponerlo)
    try:
        firma = evento.get("signature", {})
        propiedades = firma.get("properties", [])
        checksum = firma.get("checksum", "")
        timestamp = evento.get("timestamp", "")
        cadena = ""
        for prop in propiedades:
            valor = evento.get("data", {})
            for parte in prop.split("."):
                valor = valor.get(parte, {}) if isinstance(valor, dict) else ""
            cadena += str(valor)
        cadena += f"{timestamp}{secret}"
        calculado = hashlib.sha256(cadena.encode("utf-8")).hexdigest()
        return calculado.upper() == str(checksum).upper()
    except Exception:
        return False
