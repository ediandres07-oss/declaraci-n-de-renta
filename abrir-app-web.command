#!/bin/zsh
# App web del Formulario 210 — doble clic para abrir en el navegador.
cd "$(dirname "$0")"
echo "Iniciando la app en http://127.0.0.1:5210 … (deje esta ventana abierta)"
exec .venv/bin/python webapp.py
