"""Interfaz de línea de comandos (entrevista interactiva).

Uso:
  python run.py procesar RUTA_EXOGENA.xlsx [opciones]
  python run.py continuar [--sesion sessions/sesion.json]

Opciones principales:
  --plantilla RUTA    plantilla Excel destino (por defecto la de tests/fixtures)
  --salida RUTA       Excel de salida (por defecto output/Formulario210_<NIT>.xlsx)
  --sesion RUTA       archivo de progreso (por defecto sessions/sesion.json)
  --no-interactivo    usa los datos de la exógena sin preguntar (para demos/tests)
  --respuestas RUTA   JSON {clave: valor} con respuestas pre-cargadas
"""
import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .entrevista import (SECCIONES, Sesion, aplicar_respuesta, leer_valor,
                         mapear_exogena_a_datos)
from .exogena_parser import (ExogenaError, calcular_topes_propios,
                             evaluar_obligacion_declarar, parsear_exogena)
from .excel_writer import escribir_formulario
from .motor_calculo import calcular, resumen_texto
from .parametros import Parametros

BASE = Path(__file__).resolve().parent.parent
PLANTILLA_DEFAULT = BASE / "tests" / "fixtures" / "Plantilla renta naturales 2025 - ITGS.xlsx"
SESION_DEFAULT = BASE / "sessions" / "sesion.json"

console = Console()

AVISO_LEGAL = (
    "[bold yellow]AVISO:[/bold yellow] esta herramienta genera un [bold]BORRADOR de apoyo[/bold] "
    "de la Declaración de Renta (Formulario 210). NO reemplaza la asesoría de un contador o "
    "abogado tributarista. Verifique la UVT, topes y tarifas contra la normativa DIAN vigente "
    "antes de presentar una declaración real. Sus datos se procesan solo localmente."
)


def _preguntar_monto(texto: str, defecto: float = 0.0, ayuda: str = "") -> float:
    import questionary
    if ayuda:
        console.print(f"  [dim]{ayuda}[/dim]")
    while True:
        r = questionary.text(f"{texto} [{defecto:,.0f}]:").ask()
        if r is None:
            raise KeyboardInterrupt
        r = r.strip().replace(".", "").replace(",", "").replace("$", "")
        if not r:
            return defecto
        try:
            return float(r)
        except ValueError:
            console.print("[red]Valor no numérico, intente de nuevo.[/red]")


def _mostrar_resumen_exogena(sesion: Sesion, params: Parametros) -> None:
    ex = sesion.exogena
    console.print(Panel(
        f"Contribuyente: [bold]{ex.nombre}[/bold]  {ex.tipo_documento} {ex.identificacion}\n"
        f"Año gravable: {ex.anio}   Fecha de corte: {ex.fecha_corte}",
        title="Exógena cargada"))

    topes = calcular_topes_propios(ex)
    tabla = Table(title="Topes (obligación de declarar)")
    tabla.add_column("Tope"); tabla.add_column("Reporte DIAN", justify="right")
    tabla.add_column("Recalculado", justify="right")
    for clave, nombre in [("ingresos", "1. Ingresos"), ("patrimonio", "2. Patrimonio"),
                          ("consumos_tc", "3. Consumos TC"), ("consignaciones", "4. Consignaciones"),
                          ("compras", "5. Compras")]:
        dian = ex.topes_dian.get(clave)
        tabla.add_row(nombre, f"{dian:,.0f}" if dian is not None else "—",
                      f"{topes.get(clave, 0):,.0f}")
    console.print(tabla)

    razones = evaluar_obligacion_declarar(ex.topes_dian or topes, params)
    if razones:
        console.print("[bold red]Está OBLIGADO a declarar:[/bold red]")
        for r in razones:
            console.print(f"  • {r}")
    else:
        console.print("[green]Con estos topes no estaría obligado a declarar "
                      "(verifique responsabilidad de IVA).[/green]")

    for adv in ex.advertencias:
        console.print(f"[yellow]⚠ {adv}[/yellow]")


def _tabla_partidas(ex) -> None:
    tabla = Table(title="Partidas detectadas (agrupadas por renglón)")
    for col, j in [("#", "right"), ("Renglón", "left"), ("Detalle", "left"),
                   ("Informante", "left"), ("Valor", "right"), ("Nota", "left")]:
        tabla.add_column(col, justify=j, overflow="fold")
    for i, p in enumerate(sorted(ex.partidas, key=lambda q: (q.renglon_asignado or 999, q.fila))):
        estilo = "dim strike" if p.excluida else ""
        tabla.add_row(str(i), f"R{p.renglon_asignado}" if p.renglon_asignado else "—",
                      p.detalle[:50], p.informante_nombre[:28], f"{p.valor:,.0f}",
                      p.nota[:45], style=estilo)
    console.print(tabla)
    console.print("[dim]Los totales por renglón alimentan el borrador; "
                  "las partidas '—' son informativas.[/dim]")


def _resumen_editable(sesion: Sesion) -> None:
    """Permite confirmar, excluir o reasignar cada partida antes de calcular."""
    import questionary
    ex = sesion.exogena
    orden = sorted(range(len(ex.partidas)),
                   key=lambda i: (ex.partidas[i].renglon_asignado or 999, ex.partidas[i].fila))
    indice = {i: ex.partidas[j] for i, j in enumerate(orden)}
    while True:
        _tabla_partidas(ex)
        accion = questionary.select(
            "¿Qué desea hacer?",
            choices=["Confirmar y continuar", "Excluir/incluir una partida",
                     "Reasignar el renglón de una partida", "Editar el valor de una partida"],
        ).ask()
        if accion is None or accion == "Confirmar y continuar":
            sesion.resumen_confirmado = True
            return
        num = questionary.text("Número de la partida (#):").ask()
        try:
            p = indice[int(num)]
        except (ValueError, KeyError, TypeError):
            console.print("[red]Número inválido.[/red]")
            continue
        if accion.startswith("Excluir"):
            p.excluida = not p.excluida
            console.print(f"Partida {'excluida' if p.excluida else 'incluida'}: {p.detalle[:50]}")
        elif accion.startswith("Reasignar"):
            r = questionary.text(
                f"Renglón destino (actual: {p.renglon_asignado}, sugeridos: {p.renglones}):").ask()
            try:
                p.renglon_asignado = int(str(r).lstrip("Rr"))
            except (ValueError, TypeError):
                console.print("[red]Renglón inválido.[/red]")
        else:
            p.valor = _preguntar_monto("Nuevo valor", p.valor)


def _entrevista(sesion: Sesion) -> None:
    import questionary
    datos = sesion.datos
    for i, seccion in enumerate(SECCIONES):
        if i < sesion.seccion_actual:
            continue
        console.rule(f"[bold]{seccion['titulo']}[/bold]")
        for preg in seccion["preguntas"]:
            actual = leer_valor(datos, preg.clave)
            if preg.tipo == "monto":
                aplicar_respuesta(datos, preg.clave,
                                  _preguntar_monto(preg.texto, float(actual or 0), preg.ayuda))
            elif preg.tipo == "entero":
                aplicar_respuesta(datos, preg.clave,
                                  int(_preguntar_monto(preg.texto, float(actual or 0), preg.ayuda)))
            elif preg.tipo == "bool":
                r = questionary.confirm(preg.texto, default=bool(actual)).ask()
                if r is None:
                    raise KeyboardInterrupt
                aplicar_respuesta(datos, preg.clave, r)
            else:
                r = questionary.text(f"{preg.texto} [{actual or ''}]:").ask()
                if r is None:
                    raise KeyboardInterrupt
                aplicar_respuesta(datos, preg.clave, r.strip() or (actual or ""))
        sesion.seccion_actual = i + 1
        sesion.guardar(SESION_DEFAULT if not hasattr(sesion, "_ruta") else sesion._ruta)
    sesion.seccion_actual = -1


def _finalizar(sesion: Sesion, params: Parametros, plantilla: Path, salida: Path) -> None:
    liq = calcular(sesion.datos, params)
    console.print()
    console.print(resumen_texto(liq, params))
    ruta = escribir_formulario(plantilla, salida, sesion.datos, liq, sesion.exogena)
    console.print(f"\n[bold green]✔ Excel generado:[/bold green] {ruta}")
    try:
        from .exogena_parser import calcular_topes_propios as _topes
        from .resumen_pdf import generar_resumen_pdf
        razones = []
        if sesion.exogena:
            razones = evaluar_obligacion_declarar(
                sesion.exogena.topes_dian or _topes(sesion.exogena), params)
        pdf = generar_resumen_pdf(ruta.with_name(ruta.stem + "_resumen.pdf"),
                                  sesion.datos, liq, params, sesion.exogena, razones)
        console.print(f"[green]✔ Resumen ejecutivo PDF:[/green] {pdf}")
        from .formulario_pdf import generar_formulario_pdf
        f210 = generar_formulario_pdf(ruta.with_suffix(".pdf"), sesion.datos, liq, params)
        console.print(f"[green]✔ Formulario 210 en PDF (borrador):[/green] {f210}")
    except ImportError:
        console.print("[yellow]reportlab no instalado: se omitió el PDF "
                      "(pip install reportlab).[/yellow]")
    log = ruta.with_suffix(".log.txt")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(resumen_texto(liq, params) + "\n\nDETALLE DEL CÁLCULO:\n")
        fh.write("\n".join(liq.detalle))
        fh.write("\n\nTRAZABILIDAD EXÓGENA:\n")
        if sesion.exogena:
            for p in sesion.exogena.partidas:
                destino = f"R{p.renglon_asignado}" if p.renglon_asignado else "(informativa)"
                marca = " [EXCLUIDA]" if p.excluida else ""
                fh.write(f"fila {p.fila:>3} → {destino:<6} {p.valor:>16,.0f}  "
                         f"{p.detalle[:60]}{marca}\n")
    console.print(f"[green]✔ Log de liquidación y trazabilidad:[/green] {log}")


def _respuestas_json(sesion: Sesion, ruta: Path) -> None:
    """Aplica respuestas pre-cargadas desde un JSON {clave: valor}."""
    with open(ruta, "r", encoding="utf-8") as fh:
        respuestas = json.load(fh)
    for clave, valor in respuestas.items():
        aplicar_respuesta(sesion.datos, clave, valor)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="declaracion-renta", description=__doc__)
    sub = parser.add_subparsers(dest="comando")

    pr = sub.add_parser("procesar", help="procesa una exógena y genera el borrador")
    pr.add_argument("exogena", help="ruta del reporte de exógena (.xlsx)")
    pr.add_argument("--plantilla", default=str(PLANTILLA_DEFAULT))
    pr.add_argument("--salida", default="")
    pr.add_argument("--sesion", default=str(SESION_DEFAULT))
    pr.add_argument("--anio", type=int, default=2025)
    pr.add_argument("--no-interactivo", action="store_true",
                    help="sin preguntas: usa solo la exógena y --respuestas")
    pr.add_argument("--respuestas", default="",
                    help="JSON con respuestas pre-cargadas (clave: valor)")

    co = sub.add_parser("continuar", help="retoma una sesión guardada")
    co.add_argument("--sesion", default=str(SESION_DEFAULT))
    co.add_argument("--plantilla", default=str(PLANTILLA_DEFAULT))
    co.add_argument("--salida", default="")
    co.add_argument("--anio", type=int, default=2025)

    args = parser.parse_args(argv)
    if not args.comando:
        parser.print_help()
        return 1

    params = Parametros.cargar(args.anio)
    console.print(Panel(AVISO_LEGAL, title="Declaración de Renta — Formulario 210",
                        border_style="yellow"))

    try:
        if args.comando == "procesar":
            try:
                exogena = parsear_exogena(args.exogena)
            except ExogenaError as exc:
                console.print(f"[bold red]Error leyendo la exógena:[/bold red] {exc}")
                return 2
            sesion = Sesion(exogena=exogena, datos=mapear_exogena_a_datos(exogena, params))
            sesion._ruta = Path(args.sesion)
            _mostrar_resumen_exogena(sesion, params)
            if args.respuestas:
                _respuestas_json(sesion, Path(args.respuestas))
            if args.no_interactivo:
                _tabla_partidas(exogena)
                sesion.resumen_confirmado = True
                sesion.seccion_actual = -1
            else:
                _resumen_editable(sesion)
                sesion.guardar(sesion._ruta)
                _entrevista(sesion)
            sesion.guardar(sesion._ruta)
        else:  # continuar
            ruta = Path(args.sesion)
            if not ruta.exists():
                console.print(f"[red]No existe la sesión {ruta}.[/red]")
                return 2
            sesion = Sesion.cargar(ruta)
            sesion._ruta = ruta
            console.print(f"[green]Sesión retomada[/green] (sección "
                          f"{sesion.seccion_actual if sesion.seccion_actual >= 0 else 'finalizada'}).")
            if not sesion.resumen_confirmado:
                _resumen_editable(sesion)
            if sesion.seccion_actual >= 0:
                _entrevista(sesion)
            sesion.guardar(ruta)

        nit = sesion.datos.contribuyente.nit or "sin_nit"
        salida = Path(args.salida) if args.salida else BASE / "output" / f"Formulario210_{nit}.xlsx"
        _finalizar(sesion, params, Path(args.plantilla), salida)
        return 0
    except KeyboardInterrupt:
        try:
            sesion.guardar(Path(args.sesion))
            console.print(f"\n[yellow]Entrevista interrumpida. Progreso guardado en "
                          f"{args.sesion}; retome con:[/yellow] python run.py continuar")
        except Exception:
            pass
        return 130


if __name__ == "__main__":
    sys.exit(main())
