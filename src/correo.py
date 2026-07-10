"""Envío de correos y lógica de recordatorios de declaración de renta.

- cargar_config_email(): lee config/email.yaml.
- enviar_email(...): manda un correo HTML por SMTP.
- plantilla_recordatorio(...): arma el HTML del recordatorio.
- recordatorios_pendientes(usuarios, hoy): decide a quién le toca hoy y de qué
  tipo (30 días / 7 días), sin enviar todavía.

El script enviar_recordatorios.py (en la raíz) usa estas funciones y se
programa para correr una vez al día.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import List, Optional

import yaml

BASE = Path(__file__).resolve().parent.parent
_EMAIL_PATH = BASE / "config" / "email.yaml"

# Umbrales (días antes del vencimiento)
DIAS_AVISO_1 = 30      # primer aviso
DIAS_AVISO_2 = 7       # aviso urgente

MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_texto(f: Optional[date]) -> str:
    return f"{f.day} de {MESES[f.month]} de {f.year}" if f else ""


def cargar_config_email() -> dict:
    if _EMAIL_PATH.exists():
        with open(_EMAIL_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def enviar_email(destino: str, asunto: str, html: str,
                 cfg: Optional[dict] = None) -> None:
    """Envía un correo HTML. Lanza excepción si el SMTP falla."""
    cfg = cfg or cargar_config_email()
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = formataddr((cfg.get("remitente_nombre", "Recordatorios"),
                              cfg.get("remitente") or cfg.get("user", "")))
    msg["To"] = destino
    if cfg.get("responder_a"):
        msg["Reply-To"] = cfg["responder_a"]
    msg.set_content("Tu cliente de correo no muestra HTML. Abre el mensaje en uno que sí.")
    msg.add_alternative(html, subtype="html")

    host, port = cfg.get("host", "smtp.gmail.com"), int(cfg.get("port", 465))
    if cfg.get("ssl", True):
        with smtplib.SMTP_SSL(host, port) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as s:
            s.starttls()
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)


def plantilla_recordatorio(nombre: str, limite: date, dias: int,
                           urgente: bool) -> tuple[str, str]:
    """Devuelve (asunto, html) del recordatorio."""
    primer_nombre = (nombre or "").split()[0] if nombre else ""
    saludo = f"Hola {primer_nombre}," if primer_nombre else "Hola,"
    fecha = fecha_texto(limite)
    azul, dorado, rojo = "#123f6b", "#e8a413", "#c0392b"
    color = rojo if urgente else azul

    if urgente:
        asunto = f"⏰ Tu declaración de renta vence en {dias} días ({fecha})"
        titular = f"Faltan {dias} días para tu vencimiento"
        cuerpo = ("Esta es la última semana para presentar tu declaración de renta "
                  "sin sanción. Después de la fecha, la DIAN cobra multa por "
                  "extemporaneidad más intereses.")
    else:
        asunto = f"📅 Tu declaración de renta vence el {fecha}"
        titular = f"Te faltan {dias} días para declarar"
        cuerpo = ("Te recordamos que se acerca tu fecha límite para presentar la "
                  "declaración de renta. Ve preparando tus documentos con tiempo "
                  "para evitar afanes y sanciones.")

    html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f5f7fa;
      font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1e2b3a">
      <div style="max-width:560px;margin:0 auto;padding:24px">
        <div style="background:#fff;border-radius:16px;overflow:hidden;
          box-shadow:0 6px 20px rgba(18,63,107,.08)">
          <div style="background:{color};color:#fff;padding:22px 26px">
            <div style="font-size:1.4rem">🧾</div>
            <div style="font-size:1.15rem;font-weight:700;margin-top:6px">{titular}</div>
          </div>
          <div style="padding:24px 26px;font-size:.95rem;line-height:1.6">
            <p>{saludo}</p>
            <p>{cuerpo}</p>
            <div style="background:#f5f7fa;border-radius:12px;padding:18px;text-align:center;margin:18px 0">
              <div style="font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#7b8a9c">
                Tu fecha límite</div>
              <div style="font-size:1.5rem;font-weight:800;color:{color};margin-top:4px">{fecha}</div>
            </div>
            <p style="text-align:center;margin:22px 0">
              <a href="#" style="background:{dorado};color:#fff;text-decoration:none;
                padding:13px 26px;border-radius:10px;font-weight:700;display:inline-block">
                Preparar mi declaración</a>
            </p>
            <p style="font-size:.82rem;color:#5a6b7f">¿Prefieres que un asesor la presente por ti?
              Responde este correo y te ayudamos.</p>
          </div>
          <div style="padding:16px 26px;border-top:1px solid #eef2f7;font-size:.72rem;color:#9db0c4">
            Recibes este aviso porque activaste los recordatorios en tu cuenta.
            Puedes desactivarlos desde "Mi cuenta".
          </div>
        </div>
      </div></body></html>"""
    return asunto, html


def notificar_solicitud_asesor(nombre: str, email_usuario: str, cedula: str,
                               limite: Optional[date], telefono: str = "",
                               cfg: Optional[dict] = None) -> bool:
    """Avisa al negocio que un usuario pidió asesor. Devuelve True si se envió.

    No lanza excepción: si el correo está deshabilitado o falla, retorna False
    (el panel /admin siempre muestra la solicitud de todos modos).
    """
    cfg = cfg or cargar_config_email()
    if not cfg.get("habilitado"):
        return False
    destino = cfg.get("notificar_a") or cfg.get("remitente") or cfg.get("user")
    if not destino:
        return False

    fecha = fecha_texto(limite) if limite else "sin calcular"
    azul = "#123f6b"
    filas = [
        ("Nombre", nombre or "—"),
        ("Correo", email_usuario or "—"),
        ("Cédula / NIT", cedula or "no la ingresó"),
        ("Vencimiento", fecha),
        ("Teléfono", telefono or "no registrado"),
    ]
    filas_html = "".join(
        f"<tr><td style='padding:6px 12px;color:#7b8a9c'>{k}</td>"
        f"<td style='padding:6px 12px;font-weight:600'>{v}</td></tr>" for k, v in filas)
    html = f"""<!DOCTYPE html><html><body style="font-family:-apple-system,Segoe UI,sans-serif;
      background:#f5f7fa;padding:24px;color:#1e2b3a">
      <div style="max-width:520px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden">
        <div style="background:{azul};color:#fff;padding:18px 22px;font-size:1.1rem;font-weight:700">
          ⚑ Un cliente pidió asesor</div>
        <div style="padding:20px 22px">
          <p style="margin:0 0 12px">Un usuario solicitó que un asesor lo contacte para su
            declaración de renta. Sus datos:</p>
          <table style="border-collapse:collapse;font-size:.9rem">{filas_html}</table>
          <p style="font-size:.82rem;color:#5a6b7f;margin-top:16px">
            Puedes responder a este correo o escribirle directamente. Este aviso también
            queda registrado en tu panel /admin.</p>
        </div>
      </div></body></html>"""
    try:
        enviar_email(destino, f"⚑ Nuevo cliente pide asesor: {nombre or email_usuario}",
                     html, cfg)
        return True
    except Exception:
        return False


@dataclass
class Pendiente:
    usuario_id: int
    email: str
    nombre: str
    limite: date
    dias: int
    urgente: bool     # True = aviso de 7 días; False = aviso de 30 días
    campo_year: str   # 'recordatorio_7_year' o 'recordatorio_30_year'


def recordatorios_pendientes(usuarios: List, hoy: date) -> List[Pendiente]:
    """Recorre usuarios y devuelve los recordatorios que tocan HOY.

    Cada usuario debe tener: id, email, nombre, fecha_limite,
    acepta_recordatorios, recordatorio_30_year, recordatorio_7_year.
    """
    pendientes: List[Pendiente] = []
    for u in usuarios:
        if not u.acepta_recordatorios or not u.email or not u.fecha_limite:
            continue
        dias = (u.fecha_limite - hoy).days
        anio = u.fecha_limite.year

        # aviso urgente (0..7 días) — tiene prioridad
        if 0 <= dias <= DIAS_AVISO_2 and u.recordatorio_7_year != anio:
            pendientes.append(Pendiente(u.id, u.email, u.nombre or "", u.fecha_limite,
                                        dias, True, "recordatorio_7_year"))
        # primer aviso (8..30 días)
        elif DIAS_AVISO_2 < dias <= DIAS_AVISO_1 and u.recordatorio_30_year != anio:
            pendientes.append(Pendiente(u.id, u.email, u.nombre or "", u.fecha_limite,
                                        dias, False, "recordatorio_30_year"))
    return pendientes
