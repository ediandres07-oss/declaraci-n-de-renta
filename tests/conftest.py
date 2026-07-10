import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXOGENA = FIXTURES / "reporteExogena2025Elizabeth.xlsx"
PLANTILLA = FIXTURES / "Plantilla renta naturales 2025 - ITGS.xlsx"


@pytest.fixture(scope="session")
def parametros():
    from src.parametros import Parametros
    return Parametros.cargar(2025)


@pytest.fixture(scope="session")
def exogena_elizabeth():
    from src.exogena_parser import parsear_exogena
    return parsear_exogena(EXOGENA)
