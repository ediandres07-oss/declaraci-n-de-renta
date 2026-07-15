import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXOGENA = FIXTURES / "reporteExogena2025Elizabeth.xlsx"
PLANTILLA = FIXTURES / "Plantilla renta naturales 2025 - ITGS.xlsx"


@pytest.fixture(autouse=True)
def _sin_smtp_real(monkeypatch):
    """Ningún test debe enviar correo de verdad: config/email.yaml local tiene
    credenciales reales. Reemplaza el envío por un registrador en memoria; los
    tests que quieran verificar envíos pueden inspeccionar `_sin_smtp_real`."""
    import src.correo as correo
    enviados = []

    def _falso_envio(destino, asunto, html, cfg=None, adjuntos=None):
        enviados.append({"destino": destino, "asunto": asunto, "html": html,
                         "adjuntos": adjuntos or []})

    monkeypatch.setattr(correo, "enviar_email", _falso_envio)
    return enviados


@pytest.fixture(scope="session")
def parametros():
    from src.parametros import Parametros
    return Parametros.cargar(2025)


@pytest.fixture(scope="session")
def exogena_elizabeth():
    from src.exogena_parser import parsear_exogena
    return parsear_exogena(EXOGENA)
