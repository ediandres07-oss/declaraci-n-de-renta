"""Lógica de la entrevista, desacoplada de la interfaz.

Contiene:
 - el mapeo puro de los totales por renglón de la exógena hacia un
   DatosDeclaracion inicial (testable sin UI),
 - la persistencia de sesión en JSON para retomar una entrevista
   interrumpida,
 - la definición declarativa de las preguntas de la entrevista (la CLI
   las recorre; una interfaz web podría recorrerlas igual).
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .modelos import DatosDeclaracion, ResultadoExogena


def calcular_dv(nit: str) -> str:
    """Dígito de verificación DIAN (Orden Administrativa 4/1989)."""
    nit = "".join(c for c in str(nit) if c.isdigit())
    if not nit:
        return ""
    pesos = [3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71]
    total = sum(int(d) * pesos[i] for i, d in enumerate(reversed(nit)))
    resto = total % 11
    return str(resto if resto < 2 else 11 - resto)


def separar_nombre_dian(nombre_completo: str):
    """Heurística para el formato DIAN 'APELLIDO1 APELLIDO2 NOMBRE1 [OTROS]'.

    Devuelve (primer_apellido, segundo_apellido, primer_nombre, otros_nombres).
    Es solo un precargado editable: apellidos compuestos requieren corrección manual.
    """
    partes = [x for x in str(nombre_completo or "").strip().split() if x]
    if not partes:
        return "", "", "", ""
    if len(partes) == 1:
        return partes[0], "", "", ""
    if len(partes) == 2:
        return partes[0], "", partes[1], ""
    if len(partes) == 3:
        return partes[0], partes[1], partes[2], ""
    return partes[0], partes[1], partes[2], " ".join(partes[3:])

# Renglón del F210 → (atributo de DatosDeclaracion, sub-objeto o None)
_MAPA_RENGLONES = {
    29: ("patrimonio_bruto", None),
    30: ("deudas", None),
    32: ("ingresos_brutos", "trabajo"),
    33: ("incrngo", "trabajo"),
    35: ("rentas_exentas_afc_fvp", "trabajo"),
    36: ("otras_rentas_exentas", "trabajo"),
    38: ("intereses_vivienda", "trabajo"),
    39: ("otras_deducciones", "trabajo"),
    43: ("ingresos_brutos", "honorarios"),
    44: ("incrngo", "honorarios"),
    45: ("costos_deducciones", "honorarios"),
    58: ("ingresos_brutos", "capital"),
    59: ("incrngo", "capital"),
    60: ("costos_deducciones", "capital"),
    74: ("ingresos_brutos", "no_laboral"),
    75: ("devoluciones", "no_laboral"),
    76: ("incrngo", "no_laboral"),
    77: ("costos_deducciones", "no_laboral"),
    99: ("pension_ingresos", None),
    100: ("pension_incrngo", None),
    104: ("dividendos_2016_anteriores", None),
    107: ("dividendos_sub1", None),
    108: ("dividendos_sub2", None),
    112: ("go_ingresos", None),
    132: ("retenciones", None),
    28: ("compras_factura_electronica", None),
}


def mapear_exogena_a_datos(exogena: ResultadoExogena,
                           parametros=None) -> DatosDeclaracion:
    """Construye el DatosDeclaracion inicial desde las partidas confirmadas.

    Nota: R28 en la exógena es el TOTAL de compras con factura electrónica;
    el motor le aplica el 1% con su tope. Los renglones no mapeados quedan
    para la entrevista.

    Si se pasa `parametros` y su componente_inflacionario > 0, se calcula
    automáticamente el INCRNGO de rentas de capital (R59) como ese % de los
    rendimientos financieros: las partidas que la propia exógena marca con
    R58 y R59 a la vez (Arts. 38, 40-1 y 41 E.T. — solo personas naturales
    no obligadas a llevar contabilidad).
    """
    datos = DatosDeclaracion()
    datos.contribuyente.nit = exogena.identificacion
    datos.contribuyente.dv = calcular_dv(exogena.identificacion)
    ap1, ap2, nom1, otros = separar_nombre_dian(exogena.nombre)
    datos.contribuyente.primer_apellido = ap1
    datos.contribuyente.segundo_apellido = ap2
    datos.contribuyente.primer_nombre = nom1
    datos.contribuyente.otros_nombres = otros
    for renglon, total in exogena.total_por_renglon().items():
        destino = _MAPA_RENGLONES.get(renglon)
        if destino is None:
            continue
        attr, sub = destino
        objetivo = getattr(datos, sub) if sub else datos
        setattr(objetivo, attr, getattr(objetivo, attr) + total)

    if parametros is not None and parametros.componente_inflacionario > 0:
        rendimientos = sum(
            p.valor for p in exogena.partidas_activas()
            if p.renglon_asignado == 58 and 59 in p.renglones
        )
        if rendimientos > 0:
            datos.capital.incrngo += round(rendimientos * parametros.componente_inflacionario)
    return datos


# ----------------------------------------------------------------------
# Preguntas de la entrevista (declarativas; la UI decide cómo mostrarlas)
# ----------------------------------------------------------------------

@dataclass
class Pregunta:
    clave: str            # ruta al campo: "contribuyente.primer_apellido", "capital.costos_deducciones"
    texto: str
    tipo: str = "monto"   # monto | entero | texto | bool
    ayuda: str = ""


SECCIONES: List[Dict[str, Any]] = [
    {
        "titulo": "Datos del contribuyente",
        "preguntas": [
            Pregunta("contribuyente.nit", "NIT / cédula (sin dígito de verificación)", "texto"),
            Pregunta("contribuyente.dv", "Dígito de verificación (DV)", "texto"),
            Pregunta("contribuyente.primer_apellido", "Primer apellido", "texto"),
            Pregunta("contribuyente.segundo_apellido", "Segundo apellido", "texto"),
            Pregunta("contribuyente.primer_nombre", "Primer nombre", "texto"),
            Pregunta("contribuyente.otros_nombres", "Otros nombres", "texto"),
            Pregunta("contribuyente.actividad_economica", "Código actividad económica (ej. 7490)", "texto"),
            Pregunta("contribuyente.es_correccion", "¿Es una corrección de una declaración anterior?", "bool"),
        ],
    },
    {
        "titulo": "Dependientes económicos",
        "preguntas": [
            Pregunta("dependientes_nombres", "Nombres de los dependientes económicos, separados por coma "
                     "(Enter si no tiene)", "texto",
                     "Hijos <18, hijos 18-25 estudiando, cónyuge o padres en dependencia económica. "
                     "Máx. 4 generan deducción de 72 UVT c/u."),
        ],
    },
    {
        "titulo": "Patrimonio no reportado por terceros",
        "preguntas": [
            Pregunta("patrimonio_extra", "Valor de activos ADICIONALES al detectado en exógena "
                     "(vehículos, inmuebles sin avalúo reportado, acciones no listadas, reajustes fiscales, "
                     "muebles y enseres, efectivo)", "monto",
                     "La exógena solo trae lo que terceros reportan. Sume aquí lo que falte."),
            Pregunta("deudas_extra", "Deudas PROPIAS no reportadas por terceros (a 31 de diciembre)", "monto"),
        ],
    },
    {
        "titulo": "Costos, deducciones y rentas exentas no certificados",
        "preguntas": [
            Pregunta("trabajo.intereses_vivienda", "Intereses de crédito de vivienda pagados (certificado banco)", "monto"),
            Pregunta("trabajo.otras_deducciones", "Otras deducciones imputables a rentas de trabajo "
                     "(medicina prepagada hasta 16 UVT/mes, etc.)", "monto"),
            Pregunta("honorarios.costos_deducciones", "Costos y gastos de honorarios / actividad independiente", "monto"),
            Pregunta("capital.costos_deducciones", "Costos y deducciones de rentas de capital", "monto"),
            Pregunta("no_laboral.costos_deducciones", "Costos y deducciones de rentas no laborales", "monto"),
            Pregunta("capital.incrngo", "INCRNGO de rentas de capital (R59) — componente inflacionario", "monto",
                     "Precalculado con el % del decreto anual (config) sobre los rendimientos "
                     "financieros marcados R58|R59 en la exógena. Enter para aceptar o corrija."),
        ],
    },
    {
        "titulo": "Pensiones",
        "preguntas": [
            Pregunta("pension_ingresos", "Ingresos por pensiones del país o del exterior", "monto"),
            Pregunta("pension_exenta", "Parte exenta de la pensión (hasta 1.000 UVT mensuales — verificar)", "monto"),
        ],
    },
    {
        "titulo": "Ganancias ocasionales",
        "preguntas": [
            Pregunta("go_ingresos", "Ingresos por ganancias ocasionales (herencias, loterías, venta de "
                     "activos poseídos >2 años)", "monto"),
            Pregunta("go_costos", "Costo fiscal de esos activos", "monto"),
            Pregunta("go_exentas", "Parte exenta o no gravada", "monto"),
            Pregunta("go_loterias", "De los ingresos anteriores, ¿cuánto es loterías/rifas/apuestas? (tarifa 20%)", "monto"),
        ],
    },
    {
        "titulo": "Historial y saldos del año anterior",
        "preguntas": [
            Pregunta("saldo_favor_anterior", "Saldo a favor de la declaración del año anterior (sin devolución)", "monto"),
            Pregunta("anticipo_anterior", "Anticipo de renta liquidado en la declaración del año anterior (R133 de esa declaración)", "monto"),
            Pregunta("impuesto_neto_anio_anterior", "Impuesto NETO de renta del año anterior (para calcular el anticipo)", "monto"),
            Pregunta("numero_anio_declaracion", "¿Cuántos años lleva declarando? 1=primera vez, 2=segundo año, 3=tercero o más", "entero"),
            Pregunta("descuento_donaciones", "Descuento tributario por donaciones certificadas", "monto"),
        ],
    },
]


def aplicar_respuesta(datos: DatosDeclaracion, clave: str, valor: Any) -> None:
    """Escribe una respuesta en DatosDeclaracion; claves 'extra' se acumulan."""
    if clave == "patrimonio_extra":
        datos.patrimonio_bruto += float(valor or 0)
        return
    if clave == "deudas_extra":
        datos.deudas += float(valor or 0)
        return
    if clave == "dependientes_nombres":
        nombres = [n.strip() for n in str(valor or "").split(",") if n.strip()]
        datos.dependientes_detalle = nombres
        datos.dependientes = len(nombres)
        return
    partes = clave.split(".")
    objetivo = datos
    for parte in partes[:-1]:
        objetivo = getattr(objetivo, parte)
    setattr(objetivo, partes[-1], valor)


def leer_valor(datos: DatosDeclaracion, clave: str) -> Any:
    if clave in ("patrimonio_extra", "deudas_extra"):
        return 0
    if clave == "dependientes_nombres":
        return ", ".join(datos.dependientes_detalle)
    objetivo = datos
    partes = clave.split(".")
    for parte in partes[:-1]:
        objetivo = getattr(objetivo, parte)
    return getattr(objetivo, partes[-1])


# ----------------------------------------------------------------------
# Persistencia de sesión (retomar entrevista interrumpida)
# ----------------------------------------------------------------------

@dataclass
class Sesion:
    """Estado completo del flujo, serializable a JSON."""
    exogena: Optional[ResultadoExogena] = None
    datos: DatosDeclaracion = field(default_factory=DatosDeclaracion)
    seccion_actual: int = 0          # índice en SECCIONES; -1 = entrevista terminada
    resumen_confirmado: bool = False

    def guardar(self, ruta: Path) -> None:
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        estado = {
            "exogena": self.exogena.to_dict() if self.exogena else None,
            "datos": self.datos.to_dict(),
            "seccion_actual": self.seccion_actual,
            "resumen_confirmado": self.resumen_confirmado,
        }
        with open(ruta, "w", encoding="utf-8") as fh:
            json.dump(estado, fh, ensure_ascii=False, indent=2, default=str)

    @classmethod
    def cargar(cls, ruta: Path) -> "Sesion":
        with open(ruta, "r", encoding="utf-8") as fh:
            estado = json.load(fh)
        return cls(
            exogena=ResultadoExogena.from_dict(estado["exogena"]) if estado.get("exogena") else None,
            datos=DatosDeclaracion.from_dict(estado["datos"]),
            seccion_actual=estado.get("seccion_actual", 0),
            resumen_confirmado=estado.get("resumen_confirmado", False),
        )
