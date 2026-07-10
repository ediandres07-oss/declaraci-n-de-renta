#!/usr/bin/env python3
"""Punto de entrada: python run.py procesar <exogena.xlsx>"""
import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
