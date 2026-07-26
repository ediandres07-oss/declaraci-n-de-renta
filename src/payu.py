"""Integración con PayU Latam (Colombia) — WebCheckout.

El contador paga en la página segura de PayU (no manejamos tarjetas). El flujo:

  1. Se crea la orden (referenceCode = orden_id) con su valor.
  2. `parametros_checkout()` arma el formulario firmado que se autoenvía a PayU.
  3. PayU cobra y llama nuestra `confirmationUrl` (servidor-a-servidor, POST):
     `confirmacion_valida()` verifica la firma y `aprobada()` dice si quedó pagada.
  4. Si aprobó, se crea la suscripción y se entrega la licencia por correo.

Credenciales (merchantId, accountId, apiKey) NO van en el código: se leen de
config/payu.yaml (o /etc/secrets/payu.yaml como Secret File en Render).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
_PATHS = [BASE / "config" / "payu.yaml", Path("/etc/secrets/payu.yaml"), BASE / "payu.yaml"]

URL_PROD = "https://checkout.payulatam.com/ppp-web-gateway-payu/"
URL_TEST = "https://sandbox.checkout.payulatam.com/ppp-web-gateway-payu/"


def cargar_config() -> dict:
    import yaml
    for ruta in _PATHS:
        if ruta.exists():
            try:
                return yaml.safe_load(open(ruta, encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def activo(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else cargar_config()
    return bool(cfg.get("merchant_id") and cfg.get("account_id") and cfg.get("api_key"))


def _monto(valor) -> str:
    """PayU espera el monto con 2 decimales (p.ej. 349000.00)."""
    return f"{float(valor):.2f}"


def _firma(api_key: str, merchant_id: str, referencia: str, monto: str, moneda: str) -> str:
    cadena = f"{api_key}~{merchant_id}~{referencia}~{monto}~{moneda}"
    return hashlib.md5(cadena.encode("utf-8")).hexdigest()


def parametros_checkout(orden_id: str, valor, descripcion: str, email_comprador: str,
                        base_url: str, cfg: dict | None = None) -> dict:
    """Devuelve {url, campos} para autoenviar el formulario a PayU."""
    cfg = cfg if cfg is not None else cargar_config()
    moneda = cfg.get("moneda", "COP")
    monto = _monto(valor)
    es_test = bool(cfg.get("test", False))
    campos = {
        "merchantId": str(cfg["merchant_id"]),
        "accountId": str(cfg["account_id"]),
        "description": descripcion[:255],
        "referenceCode": orden_id,
        "amount": monto,
        "tax": "0",
        "taxReturnBase": "0",
        "currency": moneda,
        "signature": _firma(cfg["api_key"], str(cfg["merchant_id"]), orden_id, monto, moneda),
        "test": "1" if es_test else "0",
        "buyerEmail": email_comprador or "",
        "responseUrl": f"{base_url}/payu/respuesta",
        "confirmationUrl": f"{base_url}/payu/confirmacion",
    }
    return {"url": URL_TEST if es_test else URL_PROD, "campos": campos}


def _valor_para_firma(valor: str) -> list:
    """PayU firma la confirmación con el valor redondeado a 1 decimal en algunos
    casos (p.ej. 349000.00 → 349000.0). Devolvemos los formatos a aceptar."""
    formas = {valor}
    try:
        f = float(valor)
        formas.add(f"{f:.1f}")   # 349000.0
        formas.add(f"{f:.2f}")   # 349000.00
    except (TypeError, ValueError):
        pass
    return list(formas)


def confirmacion_valida(params: dict, cfg: dict | None = None) -> bool:
    """Verifica la firma del webhook de confirmación de PayU."""
    cfg = cfg if cfg is not None else cargar_config()
    sign = (params.get("sign") or "").lower()
    merchant_id = str(params.get("merchant_id") or "")
    referencia = params.get("reference_sale") or ""
    moneda = params.get("currency") or cfg.get("moneda", "COP")
    estado = str(params.get("state_pol") or "")
    if not (sign and referencia):
        return False
    for val in _valor_para_firma(params.get("value") or ""):
        cadena = f"{cfg.get('api_key','')}~{merchant_id}~{referencia}~{val}~{moneda}~{estado}"
        if hashlib.md5(cadena.encode("utf-8")).hexdigest().lower() == sign:
            return True
    return False


def aprobada(params: dict) -> bool:
    """state_pol = 4 (APROBADA)."""
    return str(params.get("state_pol") or "") == "4"


def estado_texto(params: dict) -> str:
    return {"4": "aprobada", "6": "rechazada", "5": "expirada",
            "7": "pendiente"}.get(str(params.get("state_pol") or ""), "desconocido")
