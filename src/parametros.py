"""Carga de parámetros normativos desde config/parametros_<año>.yaml.

Los valores normativos (UVT, topes, tabla Art. 241) viven como datos,
nunca hardcodeados: cambian cada año gravable.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class Parametros:
    """Acceso tipado a los parámetros normativos de un año gravable."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    @classmethod
    def cargar(cls, anio: int = 2025, ruta: Optional[Path] = None) -> "Parametros":
        ruta = ruta or CONFIG_DIR / f"parametros_{anio}.yaml"
        if not ruta.exists():
            raise FileNotFoundError(
                f"No existe el archivo de parámetros {ruta}. "
                f"Cree uno a partir de config/parametros_2025.yaml para el año {anio}."
            )
        with open(ruta, "r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    # -- básicos ------------------------------------------------------
    @property
    def anio_gravable(self) -> int:
        return int(self._data["anio_gravable"])

    @property
    def uvt(self) -> float:
        return float(self._data["uvt"])

    def a_uvt(self, pesos: float) -> float:
        return pesos / self.uvt

    def a_pesos(self, uvt: float) -> float:
        return uvt * self.uvt

    # -- topes obligado a declarar ------------------------------------
    @property
    def topes_declarar_uvt(self) -> Dict[str, float]:
        return dict(self._data["topes_obligado_declarar_uvt"])

    def topes_declarar_pesos(self) -> Dict[str, float]:
        return {k: v * self.uvt for k, v in self.topes_declarar_uvt.items()}

    # -- límites cédula general ---------------------------------------
    @property
    def limite_40_porcentaje(self) -> float:
        return float(self._data["limite_rentas_exentas_deducciones"]["porcentaje"])

    @property
    def limite_40_tope_uvt(self) -> float:
        return float(self._data["limite_rentas_exentas_deducciones"]["tope_uvt"])

    @property
    def exenta_25_porcentaje(self) -> float:
        return float(self._data["renta_exenta_laboral_25"]["porcentaje"])

    @property
    def exenta_25_tope_uvt(self) -> float:
        return float(self._data["renta_exenta_laboral_25"]["tope_uvt"])

    @property
    def dependientes_uvt(self) -> float:
        return float(self._data["deduccion_dependientes"]["uvt_por_dependiente"])

    @property
    def dependientes_max(self) -> int:
        return int(self._data["deduccion_dependientes"]["maximo_dependientes"])

    @property
    def dependientes_387_pct(self) -> float:
        return float(self._data["deduccion_dependientes_387"]["porcentaje"])

    @property
    def dependientes_387_tope_uvt(self) -> float:
        return float(self._data["deduccion_dependientes_387"]["tope_uvt"])

    @property
    def descuento_donaciones_pct(self) -> float:
        return float(self._data.get("descuento_donaciones", {}).get("porcentaje", 0.25))

    @property
    def descuento_258_tope_pct(self) -> float:
        return float(self._data.get("descuento_donaciones", {}).get("tope_pct_impuesto", 0.25))

    @property
    def factura_electronica_pct(self) -> float:
        return float(self._data["deduccion_factura_electronica"]["porcentaje"])

    @property
    def factura_electronica_tope_uvt(self) -> float:
        return float(self._data["deduccion_factura_electronica"]["tope_uvt"])

    @property
    def renta_presuntiva_tasa(self) -> float:
        return float(self._data["renta_presuntiva"]["tasa"])

    # -- tablas de impuesto -------------------------------------------
    @property
    def tabla_art_241(self) -> List[Dict[str, Any]]:
        return list(self._data["tabla_art_241"])

    @property
    def dividendos(self) -> Dict[str, Any]:
        return dict(self._data["dividendos"])

    @property
    def go_tarifa_general(self) -> float:
        return float(self._data["ganancias_ocasionales"]["tarifa_general"])

    @property
    def go_tarifa_loterias(self) -> float:
        return float(self._data["ganancias_ocasionales"]["tarifa_loterias"])

    @property
    def go_tipos(self) -> Dict[str, Any]:
        return dict(self._data["ganancias_ocasionales"].get("tipos", {}))

    def go_tipo(self, clave: str) -> Dict[str, Any]:
        """Configuración de un tipo de GO; cae en 'otra' si la clave no existe."""
        tipos = self.go_tipos
        return tipos.get(clave, tipos.get("otra", {"exencion": {"tipo": "none"}}))

    @property
    def anticipo_porcentajes(self) -> Dict[int, float]:
        a = self._data["anticipo"]
        return {
            1: float(a["porcentaje_primer_anio"]),
            2: float(a["porcentaje_segundo_anio"]),
            3: float(a["porcentaje_tercer_anio"]),
        }

    @property
    def componente_inflacionario(self) -> float:
        return float(self._data.get("componente_inflacionario", 0.0))

    # -- utilidades ----------------------------------------------------
    def impuesto_tabla(self, base_pesos: float, tabla: Optional[List[Dict[str, Any]]] = None) -> float:
        """Aplica una tabla progresiva en UVT (formato tabla_art_241) a una base en pesos."""
        if base_pesos <= 0:
            return 0.0
        tabla = tabla if tabla is not None else self.tabla_art_241
        base_uvt = self.a_uvt(base_pesos)
        for rango in tabla:
            hasta = rango.get("hasta_uvt")
            if hasta is None or base_uvt <= hasta:
                impuesto_uvt = (base_uvt - rango["desde_uvt"]) * rango["tarifa"] + rango.get("adicion_uvt", 0)
                return max(0.0, impuesto_uvt * self.uvt)
        return 0.0
