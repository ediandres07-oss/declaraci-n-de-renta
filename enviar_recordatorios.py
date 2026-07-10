#!/usr/bin/env python
"""Envía los recordatorios de vencimiento que toquen hoy. Correr una vez al día.

Uso:
    .venv/bin/python enviar_recordatorios.py            # envía los de hoy
    .venv/bin/python enviar_recordatorios.py --seco     # muestra a quién SIN enviar
    .venv/bin/python enviar_recordatorios.py --prueba correo@x.com   # correo de muestra

Programar diario (cron), p. ej. todos los días a las 8:00 a.m.:
    crontab -e
    0 8 * * *  cd /ruta/al/proyecto && .venv/bin/python enviar_recordatorios.py >> sessions/recordatorios.log 2>&1
"""
import sys
from datetime import date

from webapp import app
from src.auth import Usuario, db
from src.correo import (cargar_config_email, enviar_email, plantilla_recordatorio,
                        recordatorios_pendientes)


def enviar_prueba(destino: str) -> int:
    cfg = cargar_config_email()
    if not cfg.get("habilitado"):
        print("⚠ El correo está deshabilitado (config/email.yaml → habilitado: false).")
        print("  Activa 'habilitado: true' y pon tus datos SMTP para enviar de verdad.")
        return 1
    asunto, html = plantilla_recordatorio("Persona de Prueba", date.today().replace(
        day=min(date.today().day, 28)), 15, urgente=False)
    enviar_email(destino, "[PRUEBA] " + asunto, html, cfg)
    print(f"✔ Correo de prueba enviado a {destino}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--prueba" in args:
        i = args.index("--prueba")
        destino = args[i + 1] if i + 1 < len(args) else ""
        if not destino:
            print("Uso: --prueba correo@ejemplo.com"); return 2
        return enviar_prueba(destino)

    seco = "--seco" in args
    cfg = cargar_config_email()
    habilitado = bool(cfg.get("habilitado"))
    hoy = date.today()

    with app.app_context():
        usuarios = Usuario.query.filter_by(acepta_recordatorios=True).all()
        pendientes = recordatorios_pendientes(usuarios, hoy)

        if not pendientes:
            print(f"[{hoy}] Sin recordatorios para enviar hoy "
                  f"({len(usuarios)} usuarios con recordatorios activos).")
            return 0

        print(f"[{hoy}] {len(pendientes)} recordatorio(s) por enviar:")
        enviados, fallidos = 0, 0
        for p in pendientes:
            tipo = "URGENTE 7d" if p.urgente else "aviso 30d"
            linea = f"  · {p.email} — {tipo} — vence {p.limite} ({p.dias} días)"
            if seco or not habilitado:
                nota = "" if seco else "  [correo deshabilitado, no se envía]"
                print(linea + (" [modo seco]" if seco else nota))
                continue
            try:
                asunto, html = plantilla_recordatorio(p.nombre, p.limite, p.dias, p.urgente)
                enviar_email(p.email, asunto, html, cfg)
                # marca el año para no reenviar el mismo aviso
                u = db.session.get(Usuario, p.usuario_id)
                setattr(u, p.campo_year, p.limite.year)
                db.session.commit()
                enviados += 1
                print(linea + "  ✔ enviado")
            except Exception as exc:  # noqa: BLE001
                fallidos += 1
                print(linea + f"  ✗ ERROR: {exc}")

        if not seco and habilitado:
            print(f"[{hoy}] Enviados: {enviados}  ·  Fallidos: {fallidos}")
        elif not habilitado and not seco:
            print("\n⚠ Correo deshabilitado: no se envió nada. "
                  "Activa config/email.yaml → habilitado: true.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
