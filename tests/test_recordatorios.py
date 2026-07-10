"""Pruebas de la lógica de recordatorios de vencimiento (sin enviar correo)."""
from datetime import date, timedelta

from src.correo import (DIAS_AVISO_1, DIAS_AVISO_2, plantilla_recordatorio,
                        recordatorios_pendientes)


class _U:
    """Usuario simulado con la interfaz que espera recordatorios_pendientes."""
    def __init__(self, uid, dias, r30=None, r7=None, acepta=True,
                 email="u@x.com", nombre="Ana Gomez"):
        self.id = uid
        self.email = email
        self.nombre = nombre
        self.fecha_limite = date.today() + timedelta(days=dias) if dias is not None else None
        self.acepta_recordatorios = acepta
        self.recordatorio_30_year = r30
        self.recordatorio_7_year = r7


def test_ventanas_de_recordatorio():
    hoy = date.today()
    anio = (hoy + timedelta(days=20)).year
    casos = [
        _U(1, 45),                 # fuera de ventana
        _U(2, 20),                 # aviso 30d
        _U(3, 5),                  # urgente 7d
        _U(4, 20, r30=anio),       # ya enviado 30d este año
        _U(5, 5, r7=anio),         # ya enviado urgente este año
        _U(6, 20, acepta=False),   # no acepta
        _U(7, -3),                 # ya venció
        _U(8, DIAS_AVISO_1),       # borde 30
        _U(9, DIAS_AVISO_2),       # borde 7 (urgente)
        _U(10, None),              # sin fecha
    ]
    pend = recordatorios_pendientes(casos, hoy)
    assert {p.usuario_id for p in pend} == {2, 3, 8, 9}


def test_borde_7_es_urgente_no_30():
    hoy = date.today()
    p = recordatorios_pendientes([_U(1, DIAS_AVISO_2)], hoy)
    assert len(p) == 1 and p[0].urgente and p[0].campo_year == "recordatorio_7_year"


def test_no_reenvia_mismo_anio():
    hoy = date.today()
    anio = (hoy + timedelta(days=15)).year
    assert recordatorios_pendientes([_U(1, 15, r30=anio)], hoy) == []


def test_asuntos_diferencian_urgencia():
    hoy = date.today()
    a30, html30 = plantilla_recordatorio("Ana Gomez", hoy + timedelta(days=20), 20, False)
    a7, html7 = plantilla_recordatorio("Ana Gomez", hoy + timedelta(days=5), 5, True)
    assert "vence el" in a30
    assert "5 días" in a7
    assert "Hola Ana" in html30 and "Hola Ana" in html7
