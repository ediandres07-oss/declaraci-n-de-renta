"""Aviso de venta a Telegram (gerente.notificar_venta_telegram)."""
from src import gerente


def test_notifica_venta_arma_mensaje(monkeypatch):
    capt = {}
    monkeypatch.setattr(gerente, "_telegram_enviar",
                        lambda t: (capt.__setitem__("t", t), True)[1])
    ok = gerente.notificar_venta_telegram("o-123", {
        "plan": "presentacion", "precio": 149900, "contacto": {"nombre": "Ana"}})
    assert ok
    assert "Nueva venta" in capt["t"]
    assert "149.900" in capt["t"]          # separador de miles con punto
    assert "Ana" in capt["t"]
    assert "Declaración presentada" in capt["t"]


def test_sin_config_no_envia_ni_rompe(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert gerente.telegram_configurado() is False
    assert gerente._telegram_enviar("hola") is False
    # y la notificación de venta tampoco lanza
    assert gerente.notificar_venta_telegram("o-1", {"plan": "pdf", "precio": 49900}) is False
