"""Calendario tributario: fechas límite por cédula y corrección de años atípicos."""
from pathlib import Path

from src.calendario import cargar_calendario, fecha_limite

PLANTILLA = Path(__file__).resolve().parent / "fixtures" / "Plantilla renta naturales 2025 - ITGS.xlsx"


def test_todas_las_fechas_caen_en_el_mismo_anio():
    cal = cargar_calendario(PLANTILLA)
    assert {f.year for f in cal.values()} == {2026}


def test_cedulas_63_64_no_muestran_fecha_vencida():
    # Antes venían como 2025-09-25 (una fecha ya pasada) por un tipeo en la plantilla.
    assert fecha_limite("1063", PLANTILLA).isoformat() == "2026-09-25"
    assert fecha_limite("2064", PLANTILLA).isoformat() == "2026-09-25"
