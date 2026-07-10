"""Parser del reporte de Información Exógena de la DIAN.

Lee el .xlsx de "Consulta de información reportada por terceros" y produce
un ResultadoExogena con:
 - metadatos del consultante,
 - partidas clasificadas por renglón del Formulario 210 (columna
   "Uso declaración Sugerida", texto libre y no estandarizado),
 - los 5 "Topes" resumen calculados por la DIAN,
 - advertencias de todo lo que requiera revisión humana.

El parser valida por NOMBRE de columna, no por posición, y tolera
variaciones de formato entre años (más/menos filas de metadatos,
columnas en otro orden, montos como texto).
"""
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl

from .modelos import PartidaExogena, ResultadoExogena


class ExogenaError(Exception):
    """Error irrecuperable leyendo el archivo de exógena."""


# --- normalización de texto -------------------------------------------------

def _norm(texto: str) -> str:
    """minúsculas, sin tildes, espacios colapsados — para comparar encabezados."""
    if texto is None:
        return ""
    s = unicodedata.normalize("NFKD", str(texto))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


# Regex tolerante: "R132", "R 30", "r58", "Renglón 29", "Renglon29", "R29:"
RE_RENGLON = re.compile(r"(?:\bR|\brengl[oó]n)\s*\.?\s*(\d{2,3})\b", re.IGNORECASE)
RE_TOPE = re.compile(r"\btope\s*(\d)\b", re.IGNORECASE)

# Beneficiario económico / titularidad (columna "Información Adicional")
RE_PARTICIPACION = re.compile(r"porcentaje\s+de\s+participaci[oó]n:\s*([\d.,]+)", re.IGNORECASE)
RE_PROPIETARIOS = re.compile(r"n[uú]mero\s+(?:de\s+)?propietarios:\s*(\d+)", re.IGNORECASE)
RE_COTITULAR = re.compile(r"titular\s+secundario|cotitular|beneficiario", re.IGNORECASE)

# Encabezados esperados (normalizados) → nombre lógico
_COLUMNAS = {
    "nit": None,  # aparece dos veces; se resuelve por orden
    "nombre / razon social": "informante_nombre",
    "nombre/razon social reportada por el tercero": "informado_nombre",
    "detalle": "detalle",
    "valor": "valor",
    "uso declaracion sugerida": "uso",
    "informacion adicional": "info_adicional",
}


def _parse_valor(v) -> Optional[float]:
    """Convierte el valor de la celda a número; tolera texto con $ , . espacios."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # formato colombiano 1.234.567,89 o anglosajón 1,234,567.89
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # una coma: decimal si hay <=2 dígitos después, si no separador de miles
        entero, _, dec = s.rpartition(",")
        s = entero.replace(".", "") + ("." + dec if len(dec) <= 2 else dec)
    else:
        # puntos como separador de miles (1.234.567)
        partes = s.split(".")
        if len(partes) > 2 or (len(partes) == 2 and len(partes[1]) == 3):
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _encontrar_encabezado(ws) -> Tuple[int, Dict[str, int]]:
    """Busca la fila de encabezado de la tabla y devuelve (fila, {nombre_lógico: col})."""
    for fila in range(1, min(ws.max_row, 60) + 1):
        valores = [ (_norm(ws.cell(row=fila, column=c).value), c)
                    for c in range(1, ws.max_column + 1)
                    if ws.cell(row=fila, column=c).value is not None ]
        textos = {t for t, _ in valores}
        if "detalle" in textos and "valor" in textos:
            cols: Dict[str, int] = {}
            nits = sorted(c for t, c in valores if t == "nit")
            if len(nits) >= 2:
                cols["informante_nit"], cols["informado_nit"] = nits[0], nits[1]
            elif len(nits) == 1:
                cols["informante_nit"] = nits[0]
            for texto, col in valores:
                for esperado, logico in _COLUMNAS.items():
                    if logico and texto.startswith(esperado):
                        cols.setdefault(logico, col)
            # fallback por posición relativa para los nombres si faltaron
            if "detalle" in cols and "valor" in cols:
                return fila, cols
    raise ExogenaError(
        "No se encontró la fila de encabezado de la tabla (se buscó una fila "
        "con columnas 'Detalle' y 'Valor'). ¿Es este un reporte de exógena de la DIAN?"
    )


def _extraer_metadatos(ws, fila_encabezado: int, resultado: ResultadoExogena) -> None:
    """Lee los metadatos por etiqueta (no por posición fija) encima del encabezado."""
    for fila in range(1, fila_encabezado):
        celdas = [ws.cell(row=fila, column=c).value for c in range(1, ws.max_column + 1)]
        textos = [(i, _norm(v)) for i, v in enumerate(celdas) if v is not None]
        for i, t in textos:
            resto = [v for v in celdas[i + 1:] if v is not None]
            valor = resto[0] if resto else None
            if t.startswith("fecha corte"):
                resultado.fecha_corte = str(valor or "")
            elif "ano al que se refiere" in t or "año" in t and "consulta" in t:
                try:
                    resultado.anio = int(valor)
                except (TypeError, ValueError):
                    pass
            elif t.startswith("tipo de documento"):
                resultado.tipo_documento = str(valor or "")
            elif t.startswith("identificacion:") or t == "identificacion":
                resultado.identificacion = str(valor or "")
            elif t.startswith("nombres / razon social") or t.startswith("nombres/razon social"):
                resultado.nombre = str(valor or "")
            elif t.startswith("fecha reporte") or "fecha reporte" in t:
                resultado.fecha_reporte = str(valor or "")
        # "Fecha   Reporte:" puede estar en cualquier columna con el valor al lado
        for i, t in textos:
            if "fecha" in t and "reporte" in t:
                resto = [v for v in celdas[i + 1:] if v is not None]
                if resto:
                    resultado.fecha_reporte = str(resto[0])


# --- reglas de clasificación -------------------------------------------------

# Detalles sin código R# que sí sabemos clasificar (texto normalizado → renglón)
_REGLAS_TEXTO = [
    # aportes obligatorios del trabajador (pensión/salud) = INCRNGO rentas de trabajo
    (re.compile(r"ingresos? no constitutivos? de renta"), 33,
     "INCRNGO de rentas de trabajo (aporte obligatorio del trabajador)"),
]

_NOTA_MULTIRENGLON = (
    "La exógena sugiere más de un renglón; se asignó al primero. "
    "Revise y reasigne en el resumen editable si corresponde."
)


def _clasificar(partida: PartidaExogena) -> None:
    """Aplica las reglas de asignación de renglón a una partida.

    Regla general: el primer R# mencionado es el renglón asignado; los demás
    quedan registrados en `renglones` como sugerencias secundarias que el
    usuario confirma en el resumen editable.
    """
    uso = partida.uso_sugerido or ""
    partida.renglones = [int(m) for m in RE_RENGLON.findall(uso)]
    partida.topes = sorted({int(m) for m in RE_TOPE.findall(uso)})

    if partida.renglones:
        partida.renglon_asignado = partida.renglones[0]
        if len(set(partida.renglones)) > 1:
            partida.nota = _NOTA_MULTIRENGLON
            # caso conocido: R58 + R59 (rendimientos financieros: el ingreso va
            # completo a R58; el componente inflacionario a R59 según decreto anual)
            if set(partida.renglones) >= {58, 59}:
                partida.nota = (
                    "Rendimiento financiero: se suma completo a R58 y el componente "
                    "inflacionario (INCRNGO R59) se calcula con el % del archivo de "
                    "configuración (fijado por decreto anual)."
                )
        return

    uso_n = _norm(uso)
    for regla, renglon, nota in _REGLAS_TEXTO:
        if regla.search(uso_n):
            partida.renglon_asignado = renglon
            partida.nota = nota
            return

    if partida.topes:
        partida.nota = f"Solo informa Tope {', '.join(map(str, partida.topes))} (no suma a un renglón)."
    else:
        partida.nota = "Sin uso sugerido: partida informativa. Revise si debe declararla."


def _ajustar_beneficiario_economico(partida: PartidaExogena) -> Optional[str]:
    """Aplica la titularidad real (beneficiario económico) sobre el valor.

    La DIAN reporta el valor COMPLETO al titular principal aunque existan
    cotitulares u otros beneficiarios. Reglas:
      - 'Porcentaje de Participación: NN' < 100 → se ajusta el valor a la
        participación (se conserva el reportado en `valor_reportado`).
      - 'Número Propietarios' > 1 sin % informado → no se ajusta, pero se
        marca para revisión en el resumen editable.
      - menciones de cotitular/titular secundario/beneficiario → revisión.
    Devuelve una advertencia para el reporte, o None.
    """
    info = partida.info_adicional or ""
    m = RE_PARTICIPACION.search(info)
    if m:
        try:
            partida.participacion = float(m.group(1).replace(",", "."))
        except ValueError:
            partida.participacion = None
    m = RE_PROPIETARIOS.search(info)
    if m:
        partida.num_propietarios = int(m.group(1))

    if partida.participacion is not None and partida.participacion < 100:
        partida.valor_reportado = partida.valor
        partida.valor = round(partida.valor * partida.participacion / 100.0)
        partida.nota = (f"Ajustado al {partida.participacion:g}% de participación "
                        f"(reportado: {partida.valor_reportado:,.0f}). " + partida.nota).strip()
        return (f"Fila {partida.fila}: '{partida.detalle[:40]}' ajustada al "
                f"{partida.participacion:g}% de participación del beneficiario económico.")

    if (partida.num_propietarios or 1) > 1 and not partida.participacion:
        partida.nota = (f"Reportado 100% al titular principal pero hay "
                        f"{partida.num_propietarios} propietarios: declare solo su "
                        f"participación real (edite el valor). " + partida.nota).strip()
        return (f"Fila {partida.fila}: '{partida.detalle[:40]}' tiene "
                f"{partida.num_propietarios} propietarios y ningún % informado — "
                f"verifique el beneficiario económico y ajuste el valor.")

    if RE_COTITULAR.search(info) or RE_COTITULAR.search(partida.detalle or ""):
        partida.nota = ("Menciona cotitular/beneficiario: confirme qué parte le "
                        "corresponde como beneficiario económico real. " + partida.nota).strip()
        return (f"Fila {partida.fila}: '{partida.detalle[:40]}' menciona "
                f"cotitular/beneficiario — confirme la titularidad real.")
    return None


_TOPES_RESUMEN = {
    "tope 1": "ingresos",
    "tope 2": "patrimonio",
    "tope 3": "consumos_tc",
    "tope 4": "consignaciones",
    "tope 5": "compras",
}


def parsear_exogena(ruta, hoja: Optional[str] = None) -> ResultadoExogena:
    """Punto de entrada del parser."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise ExogenaError(f"El archivo no existe: {ruta}")
    try:
        wb = openpyxl.load_workbook(ruta, data_only=True, read_only=False)
    except Exception as exc:
        raise ExogenaError(f"No se pudo abrir el archivo (¿corrupto o no es .xlsx?): {exc}") from exc

    if hoja:
        if hoja not in wb.sheetnames:
            raise ExogenaError(f"El libro no tiene la hoja '{hoja}'. Hojas: {wb.sheetnames}")
        ws = wb[hoja]
    elif "Reporte" in wb.sheetnames:
        ws = wb["Reporte"]
    else:
        ws = wb[wb.sheetnames[0]]

    resultado = ResultadoExogena(archivo=str(ruta))
    if ws.title != "Reporte":
        resultado.advertencias.append(
            f"La hoja procesada se llama '{ws.title}' (se esperaba 'Reporte')."
        )

    fila_enc, cols = _encontrar_encabezado(ws)
    if "uso" not in cols:
        raise ExogenaError(
            "El reporte no tiene la columna 'Uso declaración Sugerida', "
            "necesaria para clasificar las partidas."
        )
    _extraer_metadatos(ws, fila_enc, resultado)

    nits_informados = set()
    for fila in range(fila_enc + 1, ws.max_row + 1):
        def _celda(nombre):
            c = cols.get(nombre)
            return ws.cell(row=fila, column=c).value if c else None

        detalle = _celda("detalle")
        valor = _parse_valor(_celda("valor"))
        if detalle is None and valor is None:
            continue  # fila vacía intercalada

        detalle_n = _norm(detalle or "")
        # filas de resumen "Tope N - ..." al final del reporte
        es_resumen = False
        for prefijo, clave in _TOPES_RESUMEN.items():
            if detalle_n.startswith(prefijo):
                if valor is not None:
                    resultado.topes_dian[clave] = valor
                es_resumen = True
                break
        if es_resumen:
            continue

        if valor is None:
            resultado.advertencias.append(
                f"Fila {fila}: el valor '{_celda('valor')}' no es numérico; partida omitida "
                f"(detalle: {detalle})."
            )
            continue

        partida = PartidaExogena(
            fila=fila,
            informante_nit=str(_celda("informante_nit") or ""),
            informante_nombre=str(_celda("informante_nombre") or ""),
            informado_nit=str(_celda("informado_nit") or ""),
            informado_nombre=str(_celda("informado_nombre") or ""),
            detalle=str(detalle or ""),
            valor=valor,
            uso_sugerido=str(_celda("uso") or ""),
            info_adicional=str(_celda("info_adicional") or ""),
        )
        _clasificar(partida)
        aviso = _ajustar_beneficiario_economico(partida)
        if aviso:
            resultado.advertencias.append(aviso)
        resultado.partidas.append(partida)
        if partida.informado_nit:
            nits_informados.add(partida.informado_nit)

    if len(nits_informados) > 1:
        resultado.advertencias.append(
            f"El reporte contiene datos de más de un NIT informado: {sorted(nits_informados)}. "
            "Verifique que el archivo corresponda a un solo contribuyente."
        )
    if not resultado.partidas:
        resultado.advertencias.append("No se encontraron partidas en el reporte.")
    return resultado


# --- agregación de topes propios y obligación de declarar --------------------

def calcular_topes_propios(resultado: ResultadoExogena) -> Dict[str, float]:
    """Reagrega los topes desde las partidas para validarlos contra el resumen DIAN.

    Nota: el Tope 2 (patrimonio) de la DIAN toma el MAYOR entre la suma de
    variables del año y el patrimonio bruto declarado el año anterior.
    """
    mapa = {1: "ingresos", 2: "patrimonio", 3: "consumos_tc", 4: "consignaciones", 5: "compras"}
    tot: Dict[str, float] = {v: 0.0 for v in mapa.values()}
    patrimonio_anterior = 0.0
    for p in resultado.partidas_activas():
        if _norm(p.detalle).startswith("total patrimonio bruto declarado"):
            # valor de comparación, no aditivo: el tope toma el MAYOR
            patrimonio_anterior = max(patrimonio_anterior, p.valor)
            continue
        for t in p.topes:
            if t in mapa:
                tot[mapa[t]] += p.valor
    # compras: la DIAN usa la factura electrónica reportada (R28) si no hay filas "Tope 5"
    if tot["compras"] == 0.0:
        for p in resultado.partidas_activas():
            if 28 in p.renglones:
                tot["compras"] += p.valor
    tot["patrimonio"] = max(tot["patrimonio"], patrimonio_anterior)
    return tot


def evaluar_obligacion_declarar(topes: Dict[str, float], parametros) -> List[str]:
    """Devuelve la lista de razones por las que el contribuyente debe declarar."""
    umbral = parametros.topes_declarar_pesos()
    razones = []
    def _fmt(v): return f"${v:,.0f}"
    if topes.get("patrimonio", 0) > umbral["patrimonio_bruto"]:
        razones.append(
            f"Patrimonio bruto {_fmt(topes['patrimonio'])} supera "
            f"{parametros.topes_declarar_uvt['patrimonio_bruto']:,.0f} UVT ({_fmt(umbral['patrimonio_bruto'])})."
        )
    if topes.get("ingresos", 0) >= umbral["ingresos_brutos"]:
        razones.append(
            f"Ingresos brutos {_fmt(topes['ingresos'])} alcanzan "
            f"{parametros.topes_declarar_uvt['ingresos_brutos']:,.0f} UVT ({_fmt(umbral['ingresos_brutos'])})."
        )
    if topes.get("consumos_tc", 0) > umbral["consumos_tarjeta_credito"]:
        razones.append(
            f"Consumos con tarjeta {_fmt(topes['consumos_tc'])} superan "
            f"{parametros.topes_declarar_uvt['consumos_tarjeta_credito']:,.0f} UVT "
            f"({_fmt(umbral['consumos_tarjeta_credito'])})."
        )
    if topes.get("consignaciones", 0) > umbral["consignaciones_inversiones"]:
        razones.append(
            f"Consignaciones/inversiones {_fmt(topes['consignaciones'])} superan "
            f"{parametros.topes_declarar_uvt['consignaciones_inversiones']:,.0f} UVT "
            f"({_fmt(umbral['consignaciones_inversiones'])})."
        )
    if topes.get("compras", 0) > umbral["compras_consumos"]:
        razones.append(
            f"Compras y consumos {_fmt(topes['compras'])} superan "
            f"{parametros.topes_declarar_uvt['compras_consumos']:,.0f} UVT ({_fmt(umbral['compras_consumos'])})."
        )
    return razones
