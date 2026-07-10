"""Modelos de datos del proyecto (independientes de la interfaz)."""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class PartidaExogena:
    """Una fila de la tabla de información reportada por terceros."""
    fila: int                       # fila en la hoja original (trazabilidad)
    informante_nit: str
    informante_nombre: str
    informado_nit: str
    informado_nombre: str
    detalle: str
    valor: float
    uso_sugerido: str               # texto libre de la columna "Uso declaración Sugerida"
    info_adicional: str = ""
    renglones: List[int] = field(default_factory=list)   # todos los R# detectados
    renglon_asignado: Optional[int] = None               # decisión final (editable)
    topes: List[int] = field(default_factory=list)       # números de tope mencionados (1-5)
    excluida: bool = False
    nota: str = ""                  # explicación de la regla aplicada / pendientes
    valor_reportado: Optional[float] = None   # valor original si `valor` fue ajustado
    participacion: Optional[float] = None     # % de participación (beneficiario económico)
    num_propietarios: Optional[int] = None    # propietarios/titulares reportados

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PartidaExogena":
        return cls(**d)


@dataclass
class ResultadoExogena:
    """Salida del parser de exógena."""
    archivo: str
    fecha_reporte: str = ""
    fecha_corte: str = ""
    anio: Optional[int] = None
    tipo_documento: str = ""
    identificacion: str = ""
    nombre: str = ""
    partidas: List[PartidaExogena] = field(default_factory=list)
    topes_dian: Dict[str, float] = field(default_factory=dict)   # resumen que trae el reporte
    advertencias: List[str] = field(default_factory=list)

    def partidas_activas(self) -> List[PartidaExogena]:
        return [p for p in self.partidas if not p.excluida]

    def total_por_renglon(self) -> Dict[int, float]:
        tot: Dict[int, float] = {}
        for p in self.partidas_activas():
            if p.renglon_asignado is not None:
                tot[p.renglon_asignado] = tot.get(p.renglon_asignado, 0) + p.valor
        return tot

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResultadoExogena":
        d = dict(d)
        d["partidas"] = [PartidaExogena.from_dict(p) for p in d.get("partidas", [])]
        return cls(**d)


@dataclass
class DatosContribuyente:
    nit: str = ""
    dv: str = ""
    primer_apellido: str = ""
    segundo_apellido: str = ""
    primer_nombre: str = ""
    otros_nombres: str = ""
    actividad_economica: str = ""
    es_correccion: bool = False
    codigo_correccion: str = ""
    formulario_anterior: str = ""


@dataclass
class SubcedulaGeneral:
    """Datos de una subcédula de la cédula general (valores en pesos)."""
    ingresos_brutos: float = 0.0
    devoluciones: float = 0.0            # solo aplica a rentas no laborales
    incrngo: float = 0.0
    costos_deducciones: float = 0.0      # costos y deducciones procedentes
    rentas_exentas_afc_fvp: float = 0.0  # aportes AFC / FVP / AVC
    otras_rentas_exentas: float = 0.0
    intereses_vivienda: float = 0.0
    otras_deducciones: float = 0.0
    rentas_pasivas_ece: float = 0.0
    compensaciones: float = 0.0

    @property
    def total_rentas_exentas(self) -> float:
        return self.rentas_exentas_afc_fvp + self.otras_rentas_exentas

    @property
    def total_deducciones(self) -> float:
        return self.intereses_vivienda + self.otras_deducciones


@dataclass
class DatosDeclaracion:
    """Todo lo necesario para liquidar el Formulario 210."""
    contribuyente: DatosContribuyente = field(default_factory=DatosContribuyente)

    trabajo: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    honorarios: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    capital: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    no_laboral: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)

    aplicar_renta_exenta_25: bool = True     # calcula automáticamente el 25% laboral

    patrimonio_bruto: float = 0.0
    deudas: float = 0.0

    # cédula de pensiones
    pension_ingresos: float = 0.0
    pension_incrngo: float = 0.0
    pension_exenta: float = 0.0              # hasta 1.000 UVT mensuales (verificar)

    # cédula de dividendos
    dividendos_2016_anteriores: float = 0.0
    dividendos_2016_incrngo: float = 0.0
    dividendos_sub1: float = 0.0             # 1a subcédula 2017+
    dividendos_sub2: float = 0.0             # 2a subcédula 2017+
    dividendos_exterior: float = 0.0
    dividendos_exterior_exentos: float = 0.0

    # ganancias ocasionales
    go_ingresos: float = 0.0
    go_costos: float = 0.0
    go_exentas: float = 0.0
    go_loterias: float = 0.0                 # parte de go_ingresos que es loterías/rifas

    # rentas gravables especiales (activos omitidos, etc.) R96
    rentas_gravables: float = 0.0
    compensaciones_perdidas: float = 0.0     # R94
    compensacion_exceso_presuntiva: float = 0.0  # R95

    # otros datos de liquidación
    dependientes: int = 0
    dependientes_detalle: List[str] = field(default_factory=list)  # nombres (informativo)
    compras_factura_electronica: float = 0.0
    retenciones: float = 0.0
    anticipo_anterior: float = 0.0           # R130
    saldo_favor_anterior: float = 0.0        # R131
    sanciones: float = 0.0                   # R135
    impuesto_neto_anio_anterior: float = 0.0  # para el cálculo del anticipo
    numero_anio_declaracion: int = 3         # 1=primera vez, 2=segundo año, 3=tercero+
    descuento_impuestos_exterior: float = 0.0   # R122
    descuento_donaciones: float = 0.0           # R123
    descuento_dividendos_otros: float = 0.0     # R124
    descuento_go_exterior: float = 0.0          # R128
    aporte_voluntario_141: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DatosDeclaracion":
        d = dict(d)
        d["contribuyente"] = DatosContribuyente(**d.get("contribuyente", {}))
        for k in ("trabajo", "honorarios", "capital", "no_laboral"):
            d[k] = SubcedulaGeneral(**d.get(k, {}))
        return cls(**d)


@dataclass
class Liquidacion:
    """Resultado del motor de cálculo: valor por renglón + detalle explicativo."""
    renglones: Dict[int, float] = field(default_factory=dict)
    detalle: List[str] = field(default_factory=list)      # explicación paso a paso
    advertencias: List[str] = field(default_factory=list)

    def r(self, numero: int) -> float:
        return self.renglones.get(numero, 0.0)

    def set(self, numero: int, valor: float, nota: str = "") -> None:
        self.renglones[numero] = round(valor)
        if nota:
            self.detalle.append(f"R{numero}: {nota} = {round(valor):,.0f}")
