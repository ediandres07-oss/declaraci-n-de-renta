"""Integración con ePayco (Colombia) — Checkout Standard.

El contador paga en la ventana segura de ePayco (checkout.js). ePayco confirma
por webhook (confirmation URL) con los parámetros x_*, que validamos con la
firma SHA256:  p_cust_id ^ p_key ^ x_ref_payco ^ x_transaction_id ^ x_amount ^ x_currency_code

Credenciales (public_key, p_cust_id, p_key) NO van en el código: se leen de
config/epayco.yaml (o Secret File en Render).
"""
from __future__ import annotations

import hashlib


def activo(cfg: dict | None) -> bool:
    cfg = cfg or {}
    return bool(cfg.get("habilitado") and cfg.get("public_key")
                and cfg.get("p_cust_id") and cfg.get("p_key"))


def datos_checkout(cfg: dict, orden_id: str, valor, descripcion: str,
                   email: str, base_url: str, nombre: str = "",
                   telefono: str = "") -> dict:
    """Parámetros para abrir el checkout.js de ePayco en el navegador.

    Nombre y teléfono van al antifraude de ePayco: un comprador sin datos de
    facturación dispara rechazos en el primer intento."""
    return {
        "public_key": str(cfg.get("public_key", "")),
        "test": "true" if cfg.get("test", True) else "false",
        "name": "Suscripción Lector tributando.co",
        "description": descripcion[:255],
        "invoice": orden_id,
        "currency": "cop",
        "amount": str(int(round(float(valor)))),
        "country": "co",
        "email_billing": email or "",
        "name_billing": (nombre or "")[:80],
        "mobilephone_billing": "".join(c for c in (telefono or "") if c.isdigit())[:15],
        "extra1": orden_id,
        "response": f"{base_url}/epayco/respuesta",
        "confirmation": f"{base_url}/epayco/confirmacion",
    }


def verificar_firma(params: dict, cfg: dict) -> bool:
    """Valida la firma x_signature del webhook de ePayco."""
    ref = params.get("x_ref_payco", "")
    txid = params.get("x_transaction_id", "")
    amount = params.get("x_amount", "")
    cur = params.get("x_currency_code", "")
    firma_recibida = (params.get("x_signature", "") or "").lower()
    if not (ref and firma_recibida):
        return False
    cadena = f"{cfg.get('p_cust_id','')}^{cfg.get('p_key','')}^{ref}^{txid}^{amount}^{cur}"
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest().lower() == firma_recibida


def aprobada(params: dict) -> bool:
    """x_cod_response = 1 (Aceptada)."""
    cod = str(params.get("x_cod_response") or params.get("x_cod_transaction_state") or "")
    return cod == "1"


def estado_texto(params: dict) -> str:
    cod = str(params.get("x_cod_response") or params.get("x_cod_transaction_state") or "")
    return {"1": "aceptada", "2": "rechazada", "3": "pendiente",
            "4": "fallida", "6": "reversada", "10": "reintento"}.get(cod, "desconocido")
