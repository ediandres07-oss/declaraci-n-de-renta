"""Gerente virtual de tributando.co.

Dos rutinas que corren solas en el servidor (hilo de webapp, candado en BD):

  - informe_diario():   TODOS los días ~7am → correo al dueño con el pulso del
                        negocio (pagos, licencias nuevas/por vencer, uso del agente).
  - contenido_semanal(): LUNES ~7am → correo con el lote de marketing de la
                        semana estilo Actualícese (el calendario DIAN dicta el
                        tema; Gemini redacta posts, short y correo).

El dueño solo lee el correo; nada se publica solo.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from src.auth import db, OrdenRegistro, SuscripcionLector

ADMIN_EMAIL = "ediandres07@gmail.com"


# ---------- informe diario ----------

def _pesos(n) -> str:
    try:
        return "$" + f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "$0"


def informe_diario() -> bool:
    """Arma y envía el informe del día anterior. True si se envió."""
    from src.correo import enviar_email

    hoy = date.today()
    ayer = hoy - timedelta(days=1)

    # Pagos: órdenes en estado "pagada" actualizadas ayer.
    pagos, total = [], 0.0
    for o in OrdenRegistro.query.filter(OrdenRegistro.actualizado >= ayer,
                                        OrdenRegistro.actualizado < hoy).all():
        try:
            d = json.loads(o.data)
        except Exception:
            continue
        if d.get("estado") == "pagada":
            valor = float(d.get("precio") or d.get("valor") or 0)
            total += valor
            pagos.append(f"{d.get('plan', '?')} · {d.get('email', '')} · {_pesos(valor)}")

    # Licencias del Lector.
    nuevas = SuscripcionLector.query.filter(SuscripcionLector.creado >= ayer,
                                            SuscripcionLector.creado < hoy).all()
    activas = SuscripcionLector.query.filter_by(activa=True).count()
    lim = hoy + timedelta(days=7)
    por_vencer = (SuscripcionLector.query
                  .filter(SuscripcionLector.vence.isnot(None),
                          SuscripcionLector.vence >= hoy,
                          SuscripcionLector.vence <= lim).all())
    per = hoy.strftime("%Y-%m")
    agente_usos = sum((s.agente_usos or 0) for s in
                      SuscripcionLector.query.filter_by(agente_periodo=per).all())

    def _lista(items, vacio):
        if not items:
            return f"<li style='color:#8a919c'>{vacio}</li>"
        return "".join(f"<li>{x}</li>" for x in items)

    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px">
      <h2 style="color:#1e2432">📊 Tributando — informe del {ayer.strftime('%d/%m/%Y')}</h2>
      <h3 style="margin-bottom:4px">💰 Pagos: {len(pagos)} · {_pesos(total)}</h3>
      <ul style="margin-top:4px">{_lista(pagos, "Sin pagos ayer.")}</ul>
      <h3 style="margin-bottom:4px">🔑 Lector</h3>
      <ul style="margin-top:4px">
        <li>Licencias activas: <b>{activas}</b></li>
        <li>Nuevas ayer: <b>{len(nuevas)}</b>{(' — ' + ', '.join(s.email for s in nuevas)) if nuevas else ''}</li>
        <li>Vencen en ≤7 días: <b>{len(por_vencer)}</b>{(' — ' + ', '.join(f"{s.email} ({s.vence})" for s in por_vencer)) if por_vencer else ''}</li>
        <li>Acciones del agente este mes: <b>{agente_usos}</b></li>
      </ul>
      <p style="color:#8a919c;font-size:.85rem">Enviado por tu gerente virtual · tributando.co</p>
    </div>"""
    enviar_email(ADMIN_EMAIL, f"📊 Tributando {ayer.strftime('%d/%m')}: "
                              f"{len(pagos)} pago(s) {_pesos(total)} · {activas} licencias",
                 html)
    return True


# ---------- contenido semanal (marketing estilo Actualícese) ----------

_PROMPT_MARKETING = (
    "Eres el editor de contenidos de tributando.co (herramientas tributarias para "
    "contadores en Colombia: Lector XML DIAN, borradores 350/300, agente IA). "
    "MENSAJE CENTRAL de la marca (úsalo siempre como promesa/llamado): "
    "'Automatiza tu contabilidad: de la DIAN directo a tu programa contable' — el "
    "Lector descarga las facturas electrónicas de la DIAN y las convierte en el "
    "plano listo para Siigo, Contai o Helisa, y arma los borradores del 350 y el 300. "
    "NO uses frases débiles tipo 'agiliza la revisión de tu exógena'; vende AUTOMATIZAR "
    "(horas de digitación → minutos).\n"
    "Como hace Actualícese, el CALENDARIO tributario dicta el tema de la semana. "
    "Hoy es {fecha}. Piensa qué vencimientos u obligaciones DIAN vienen en los "
    "próximos 15-30 días y elige EL tema más urgente. CALENDARIO 2026 (respétalo): "
    "renta PERSONAS JURÍDICAS venció en abril-mayo (NO es tema ahora); renta PERSONAS "
    "NATURALES (AG 2025) va de agosto a octubre según los dos últimos dígitos de la "
    "cédula — ESA es la temporada que arranca; además: retención mensual (350), IVA "
    "bimestral/cuatrimestral (300), autorretención Decreto 572 (vigente desde 1 jul 2026), "
    "exógena ya presentada (correcciones). UVT $52.374.\n"
    "Entrega EXACTAMENTE esto, en español claro para contadores, sin inventar fechas "
    "exactas (di 'según el último dígito del NIT' cuando aplique):\n"
    "1. TEMA DE LA SEMANA: una línea.\n"
    "2. POST LINKEDIN/FACEBOOK (100-150 palabras, cierre con llamado a probar el Lector "
    "en tributando.co/contadores).\n"
    "3. 3 ESTADOS DE WHATSAPP (1-2 líneas cada uno, directos).\n"
    "4. GUION DE SHORT (30 seg, formato: GANCHO / 3 PUNTOS / CIERRE).\n"
    "5. CORREO CORTO para contadores (asunto + 80-120 palabras, tono útil, no vendedor).\n"
    "Sin markdown decorativo excesivo; numera las secciones tal cual."
)


def contenido_semanal(ia_cfg: dict) -> bool:
    """Genera el lote de marketing con Gemini y lo envía por correo. True si salió."""
    from src.asistente import responder
    from src.correo import enviar_email

    fecha = date.today().strftime("%d de %B de %Y")
    prompt = _PROMPT_MARKETING.format(fecha=fecha)
    texto = responder([{"rol": "user", "texto": prompt}], ia_cfg)
    cuerpo = (texto or "").replace("\n", "<br>")
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px">
      <h2 style="color:#1e2432">📣 Tu lote de contenido — semana del {date.today().strftime('%d/%m/%Y')}</h2>
      <p style="color:#8a919c;font-size:.9rem">Borrador generado por tu gerente virtual.
      Revísalo, ajústalo a tu voz y publica. Nada se publica solo.</p>
      <div style="background:#faf7f0;border:1px solid #e2ddd2;border-radius:10px;padding:14px;line-height:1.55">{cuerpo}</div>
    </div>"""
    enviar_email(ADMIN_EMAIL, "📣 Contenido de la semana — tributando.co", html)
    return True
