"""Escritura del resultado sobre la plantilla Excel (hoja 'FORMULARIO 210').

Copia la plantilla ITGS y escribe:
 - los datos del contribuyente en la hoja 'Datos del contribuyente',
 - el número de dependientes en la hoja 'Dependientes ',
 - los valores calculados directamente en las celdas de cada renglón de la
   hoja 'FORMULARIO 210' (reemplaza las fórmulas por valores para que el
   archivo sea autocontenido y refleje la liquidación del motor),
 - una hoja nueva 'Trazabilidad' con el log de qué filas de la exógena
   alimentaron cada renglón.

El formato de la plantilla (estilos, merges) se preserva porque solo se
tocan valores de celdas existentes.
"""
import shutil
import warnings
from pathlib import Path
from typing import Optional

import openpyxl

from .hojas_detalle import llenar_hojas_detalle
from .modelos import DatosDeclaracion, Liquidacion, ResultadoExogena
from .parametros import Parametros

HOJA_FORMULARIO = "FORMULARIO 210"
HOJA_DATOS = "Datos del contribuyente"
HOJA_DEPENDIENTES = "Dependientes "

# Mapa renglón → celda en la hoja 'FORMULARIO 210' de la plantilla ITGS.
# Levantado inspeccionando la plantilla: cada renglón tiene su celda de valor.
CELDAS_RENGLON = {
    28: "AB12",
    29: "L13", 30: "S13", 31: "Z13",
    32: "H15", 43: "M15", 58: "S15", 74: "Z15",
    75: "Z16",
    33: "H17", 44: "M17", 59: "S17", 76: "Z17",
    45: "M18", 60: "S18", 77: "Z18",
    34: "H19", 46: "M19", 61: "S19", 78: "Z19",
    62: "S20", 79: "Z20",
    35: "H21", 47: "M21", 63: "S21", 80: "Z21",
    36: "H22", 48: "M22", 64: "S22", 81: "Z22",
    37: "H23", 49: "M23", 65: "S23", 82: "Z23",
    38: "H24", 50: "M24", 66: "S24", 83: "Z24",
    39: "H25", 51: "M25", 67: "S25", 84: "Z25",
    40: "H26", 52: "M26", 68: "S26", 85: "Z26",
    41: "H27", 53: "M27", 69: "S27", 86: "Z27",
    54: "M28", 70: "S28", 87: "Z28",
    55: "M29", 71: "S29", 88: "Z29",
    56: "M30", 72: "S30", 89: "Z30",
    42: "H31", 57: "M31", 73: "S31", 90: "Z31",
    91: "F32", 92: "L32", 93: "S32", 94: "AA32",
    95: "F33", 96: "L33", 97: "S33", 98: "AA33",
    99: "J34", 100: "J35", 101: "J36", 102: "J37", 103: "J38",
    104: "J39", 105: "J40", 106: "J41", 107: "J42", 108: "J43",
    109: "J44", 110: "J45",
    111: "J46",
    112: "J47", 113: "J48", 114: "J49", 115: "J50",
    116: "Y34", 117: "Y35", 118: "Y36", 119: "Y37", 120: "Y38", 121: "Y39",
    122: "U40", 123: "AB40", 124: "U41", 125: "AB41",
    126: "Y42", 127: "Y43", 128: "Y44", 129: "Y45",
    130: "Y46", 131: "Y47", 132: "Y48", 133: "Y49",
    134: "F51", 135: "M51", 136: "U51", 137: "AB51",
    138: "F52", 139: "M52",
    141: "AB52",
}


def escribir_formulario(
    plantilla: Path,
    salida: Path,
    datos: DatosDeclaracion,
    liq: Liquidacion,
    exogena: Optional[ResultadoExogena] = None,
    parametros: Optional[Parametros] = None,
) -> Path:
    """Genera el Excel de salida. Devuelve la ruta escrita."""
    plantilla, salida = Path(plantilla), Path(salida)
    if not plantilla.exists():
        raise FileNotFoundError(f"No existe la plantilla: {plantilla}")
    salida.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plantilla, salida)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(salida)

    # ---- datos del contribuyente ------------------------------------
    con = datos.contribuyente
    if HOJA_DATOS in wb.sheetnames:
        ws = wb[HOJA_DATOS]
        ws["C6"] = con.nit
        ws["C7"] = con.dv
        ws["C8"] = con.primer_apellido
        ws["C9"] = con.segundo_apellido
        ws["C10"] = con.primer_nombre
        ws["C11"] = con.otros_nombres
        ws["C12"] = con.actividad_economica
        ws["C13"] = "x" if con.es_correccion else ""
        ws["C14"] = con.formulario_anterior

    # ---- dependientes -------------------------------------------------
    if HOJA_DEPENDIENTES in wb.sheetnames:
        ws = wb[HOJA_DEPENDIENTES]
        ws["C3"] = datos.dependientes
        # nombres en B7:B10 (la plantilla deduce por nombre presente); si solo
        # hay conteo se escriben marcadores para no dejar los de ejemplo
        nombres = [n for n in datos.dependientes_detalle if str(n).strip()][:4]
        if not nombres and datos.dependientes > 0:
            nombres = [f"Dependiente {i+1}" for i in range(min(datos.dependientes, 4))]
        for i in range(4):
            # asignación directa: cell(value=None) no borra en openpyxl
            ws.cell(row=7 + i, column=2).value = nombres[i] if i < len(nombres) else None

    # ---- hojas de detalle: datos reales, sin ejemplos de la plantilla --
    llenar_hojas_detalle(wb, datos, liq, exogena, parametros)

    # ---- formulario 210: valores por renglón --------------------------
    if HOJA_FORMULARIO not in wb.sheetnames:
        raise ValueError(f"La plantilla no tiene la hoja '{HOJA_FORMULARIO}'.")
    ws = wb[HOJA_FORMULARIO]
    for renglon, celda in CELDAS_RENGLON.items():
        if renglon in liq.renglones:
            ws[celda] = liq.renglones[renglon]

    # ---- trazabilidad --------------------------------------------------
    if exogena is not None:
        nombre = "Trazabilidad"
        if nombre in wb.sheetnames:
            del wb[nombre]
        tz = wb.create_sheet(nombre)
        tz.append(["Fila exógena", "Renglón asignado", "Detalle", "Valor",
                   "NIT informante", "Informante", "Excluida", "Nota"])
        for p in exogena.partidas:
            tz.append([
                p.fila,
                f"R{p.renglon_asignado}" if p.renglon_asignado else "",
                p.detalle, p.valor, p.informante_nit, p.informante_nombre,
                "sí" if p.excluida else "", p.nota,
            ])
        tz.append([])
        tz.append(["Advertencias de la liquidación:"])
        for a in liq.advertencias:
            tz.append(["", a])
        for col, ancho in zip("ABCDEFGH", (12, 14, 60, 16, 14, 40, 9, 60)):
            tz.column_dimensions[col].width = ancho

    wb.save(salida)
    return salida
