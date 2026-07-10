#!/bin/zsh
# Lanzador de la app de Declaración de Renta (doble clic para abrir).
cd "$(dirname "$0")"

echo "=============================================="
echo " Declaración de Renta — Formulario 210 (2025)"
echo "=============================================="
echo
echo "Arrastre aquí el archivo de exógena (.xlsx) y presione Enter,"
echo "o presione Enter sin nada para usar el ejemplo (Elizabeth):"
read -r RUTA
RUTA=${RUTA//\'/}          # quita comillas que agrega el arrastre
RUTA=${RUTA%% }            # quita espacio final
if [[ -z "$RUTA" ]]; then
  RUTA="tests/fixtures/reporteExogena2025Elizabeth.xlsx"
fi

exec .venv/bin/python run.py procesar "$RUTA"
