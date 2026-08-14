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
class GananciaOcasional:
    """Una ganancia ocasional tipificada por origen (Arts. 300–317 E.T.).

    `tipo` es una clave del catálogo `ganancias_ocasionales.tipos` del YAML de
    parámetros, que define la tarifa y la regla de exención aplicable. El motor
    de cálculo es quien resuelve la exención contra el tope en UVT: este modelo
    no conoce la UVT del año.
    """
    tipo: str = "otra"
    descripcion: str = ""
    ingreso: float = 0.0             # valor recibido / precio de venta
    costo_fiscal: float = 0.0        # se ignora en loterías (Art. 317)
    exenta_manual: float = 0.0       # exención declarada por el usuario
    valor_catastral: float = 0.0     # venta de vivienda: tope 15.000 UVT (Art. 311-1)
    deposito_afc: bool = False       # venta de vivienda: requisito de depósito en AFC

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GananciaOcasional":
        return cls(**d)


@dataclass
class ActivoFijo:
    """Un activo fijo depreciable del comerciante (Art. 137, línea recta)."""
    descripcion: str = ""
    categoria: str = "vehiculos"   # vehiculos|maquinaria|muebles|computo|construcciones|otros
    valor: float = 0.0             # costo fiscal
    en_exogena: bool = False       # True si ya está en el patrimonio de la exógena

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ActivoFijo":
        return cls(**{k: v for k, v in d.items()
                      if k in ("descripcion", "categoria", "valor", "en_exogena")})


@dataclass
class DatosDeclaracion:
    """Todo lo necesario para liquidar el Formulario 210."""
    contribuyente: DatosContribuyente = field(default_factory=DatosContribuyente)

    trabajo: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    honorarios: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    capital: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)
    no_laboral: SubcedulaGeneral = field(default_factory=SubcedulaGeneral)

    aplicar_renta_exenta_25: bool = True     # calcula automáticamente el 25% laboral
    docente_publico: bool = False            # rector/profesor de universidad oficial:
    #                                          50% del salario exento (Art. 206 num. 9)
    # Cesantías e intereses pagados: son renta exenta (Art. 206 num. 4), con % según
    # el salario mensual promedio. Ya están incluidas en trabajo.ingresos_brutos (R32);
    # aquí se guardan aparte para calcular su exención y detraerlas de la base del 25%.
    cesantias: float = 0.0
    salario_mensual_promedio: float = 0.0    # para el tope del Art. 206-4 (0 = usar R32/12)

    patrimonio_bruto: float = 0.0
    deudas: float = 0.0

    # Comerciante (PN): costo de la mercancía vendida por juego de inventarios
    # (Arts. 62/63 E.T.). Las COMPRAS se ingresan en el módulo (compras_mercancia);
    # el CMV = compras + inv.inicial − inv.final va a costos no laborales (R77). En
    # no_laboral.costos_deducciones quedan los OTROS costos/gastos (no mercancía).
    # El inventario final es un activo → suma al patrimonio bruto (R29).
    inventario_inicial: float = 0.0
    compras_mercancia: float = 0.0
    inventario_final: float = 0.0

    # Activos fijos depreciables del comerciante — COSTO FISCAL por categoría.
    # El motor aplica la tasa del Art. 137 (línea recta) y lleva la depreciación
    # del año a costos no laborales (R77). No suma al patrimonio (el activo ya
    # viene en la exógena por su avalúo/costo). `depreciacion_manual` es para lo
    # que el contador ya tenga calculado y no encaje en las categorías.
    activo_vehiculos: float = 0.0        # 10% anual (10 años) — totales rápidos (compat)
    activo_maquinaria: float = 0.0       # 10%
    activo_muebles: float = 0.0          # 10%
    activo_equipo_computo: float = 0.0   # 20% (5 años)
    depreciacion_manual: float = 0.0
    # Lista detallada de activos fijos (uno por línea). Permite cargar los que la
    # exógena NO trae: se deprecian y, si `en_exogena=False`, suman al patrimonio.
    activos_fijos: List["ActivoFijo"] = field(default_factory=list)

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

    # ganancias ocasionales — entrada rápida (agregada, sin tipificar)
    go_ingresos: float = 0.0
    go_costos: float = 0.0
    go_exentas: float = 0.0
    go_loterias: float = 0.0                 # parte de go_ingresos que es loterías/rifas
    # ganancias ocasionales tipificadas: si trae elementos, manda sobre los campos planos
    go_partidas: List[GananciaOcasional] = field(default_factory=list)

    # rentas gravables especiales (activos omitidos, etc.) R96
    rentas_gravables: float = 0.0
    compensaciones_perdidas: float = 0.0     # R94
    compensacion_exceso_presuntiva: float = 0.0  # R95

    # Venta de activos fijos detectada en la exógena (para la nota Art. 300):
    # la DIAN la manda a GO por defecto, pero solo es ganancia ocasional si el
    # activo se poseyó 2 años o más; si menos, es renta no laboral (R74).
    venta_activos_fijos: float = 0.0

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

    def go_partidas_efectivas(self) -> List["GananciaOcasional"]:
        """Ganancias ocasionales a liquidar, tipificadas.

        Si `go_partidas` trae elementos es la fuente de verdad. Si está vacía se
        sintetizan al vuelo desde los campos planos (`go_ingresos`, `go_costos`,
        `go_exentas`, `go_loterias`), que es como los alimentan la entrevista y
        el formulario web. No se persiste la síntesis: así `to_dict()` nunca
        confunde una partida derivada con una declarada.
        """
        if self.go_partidas:
            return self.go_partidas
        partidas: List[GananciaOcasional] = []
        resto = self.go_ingresos - self.go_loterias
        if resto or self.go_costos or self.go_exentas:
            partidas.append(GananciaOcasional(
                tipo="otra", ingreso=resto,
                costo_fiscal=self.go_costos, exenta_manual=self.go_exentas))
        if self.go_loterias:
            partidas.append(GananciaOcasional(
                tipo="loteria_rifa_apuesta", ingreso=self.go_loterias))
        return partidas

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DatosDeclaracion":
        d = dict(d)
        d["contribuyente"] = DatosContribuyente(**d.get("contribuyente", {}))
        for k in ("trabajo", "honorarios", "capital", "no_laboral"):
            d[k] = SubcedulaGeneral(**d.get(k, {}))
        d["go_partidas"] = [GananciaOcasional.from_dict(g)
                            for g in d.get("go_partidas", [])]
        d["activos_fijos"] = [ActivoFijo.from_dict(a)
                              for a in d.get("activos_fijos", [])]
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
