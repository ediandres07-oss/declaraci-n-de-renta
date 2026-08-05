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

from src.auth import db, OrdenRegistro, SuscripcionLector, LeadEspera, Usuario

ADMIN_EMAIL = "ediandres07@gmail.com"


# ---------- campaña a leads (manual, con preview antes de enviar) ----------

def _es_propio(email: str) -> bool:
    """Correos del dueño / de prueba: nunca deben recibir campañas."""
    e = (email or "").strip().lower()
    return e.startswith("ediandres07") or e == "contacto@tributando.co"


def destinatarios_campana(publico: str = "contadores") -> list[str]:
    """Correos únicos para una campaña, según el público:
      - 'personas': usuarios registrados (personas naturales de renta, tabla Usuario)
      - cualquier otro: leads en espera (guía) + contadores con licencia (Lector)
    Siempre excluye los correos propios/de prueba."""
    correos = set()
    if publico == "personas":
        for u in Usuario.query.all():
            if u.email:
                correos.add(u.email.strip().lower())
    else:
        for l in LeadEspera.query.all():
            if l.email:
                correos.add(l.email.strip().lower())
        for s in SuscripcionLector.query.all():
            if s.email:
                correos.add(s.email.strip().lower())
    return sorted(c for c in correos if not _es_propio(c))


def enviar_campana(asunto: str, html: str, destinatarios: list[str]) -> dict:
    """Envía la campaña 1 a 1 (no expone correos entre destinatarios). Devuelve
    {enviados, fallidos}."""
    from src.correo import enviar_email

    enviados, fallidos = 0, []
    for correo in destinatarios:
        try:
            enviar_email(correo, asunto, html)
            enviados += 1
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{correo}: {exc}")
    return {"enviados": enviados, "fallidos": fallidos}


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


# ---------- métricas para el dashboard de /admin ----------

def metricas_negocio() -> dict:
    """Números vivos para el panel: funnel B2C (renta), pase de temporada de
    contadores y estado del Lector (pruebas/pagadas). Lee lo que ya existe."""
    from src.auth import ArchivoExogena, AccesoAutorizado, EmpresaLector, LeadExogena

    hoy = date.today()
    b2c_creadas = b2c_pagadas = 0
    b2c_ingreso = 0.0
    pase_creadas = pase_pagadas = 0
    pase_ingreso = 0.0
    lector_ord_pagadas = 0

    for o in OrdenRegistro.query.all():
        try:
            d = json.loads(o.data)
        except Exception:
            continue
        if d.get("tipo") != "orden":
            continue                                   # cargas de exógena sin orden
        plan = (d.get("plan") or "").lower()
        pagada = d.get("estado") == "pagada"
        precio = float(d.get("precio") or d.get("valor") or 0)
        if plan in ("pdf", "presentacion"):
            b2c_creadas += 1
            if pagada:
                b2c_pagadas += 1
                b2c_ingreso += precio
        elif plan == "contadores":
            pase_creadas += 1
            if pagada:
                pase_pagadas += 1
                pase_ingreso += precio
        elif plan == "lector" and pagada:
            lector_ord_pagadas += 1

    pruebas = SuscripcionLector.query.filter_by(plan="prueba", activa=True).all()
    pruebas_activas = sum(1 for s in pruebas if not s.vence or s.vence >= hoy)
    pagadas_activas = (SuscripcionLector.query
                       .filter(SuscripcionLector.plan != "prueba",
                               SuscripcionLector.activa.is_(True)).count())

    return {
        "exogenas": ArchivoExogena.query.count(),
        "leads": LeadEspera.query.count(),
        "leads_exogena": LeadExogena.query.count(),
        "b2c_creadas": b2c_creadas,
        "b2c_pagadas": b2c_pagadas,
        "b2c_conversion": round(100 * b2c_pagadas / b2c_creadas) if b2c_creadas else 0,
        "b2c_ingreso": b2c_ingreso,
        "pase_creadas": pase_creadas,
        "pase_pagadas": pase_pagadas,
        "pase_ingreso": pase_ingreso,
        "pase_accesos": AccesoAutorizado.query.count(),
        "lector_pruebas_activas": pruebas_activas,
        "lector_pagadas_activas": pagadas_activas + lector_ord_pagadas,
        "lector_empresas": EmpresaLector.query.count(),
        "ingreso_total": b2c_ingreso + pase_ingreso,
    }


# ---------- onboarding del trial del Lector (secuencia de 4 correos) ----------

SITIO = "https://tributando.co"
_DESCARGA_LECTOR = SITIO + "/descargar-lector"
_PLANES_LECTOR = SITIO + "/contadores/lector"
_ONBOARDING_PASOS = [1, 7, 20, 28]


def _wrap_correo(pill: str, cuerpo: str, cta_txt: str, cta_url: str) -> str:
    return f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:540px;margin:auto;color:#2b3242">
      <div style="background:#1e2432;padding:22px 26px;border-radius:14px 14px 0 0;text-align:center">
        <div style="font-size:20px;font-weight:800;color:#fff">Tributando<span style="color:#cdab7e">.co</span></div>
      </div>
      <div style="background:#fff;border:1px solid #e6e2d8;border-top:none;border-radius:0 0 14px 14px;padding:26px">
        <div style="display:inline-block;background:#f3ede1;color:#8a6d3b;font-weight:800;font-size:11px;padding:4px 11px;border-radius:999px;letter-spacing:.3px">{pill}</div>
        {cuerpo}
        <div style="text-align:center;margin:22px 0 6px">
          <a href="{cta_url}" style="display:inline-block;background:#c8991f;color:#fff;font-weight:800;text-decoration:none;padding:13px 30px;border-radius:11px">{cta_txt}</a>
        </div>
        <p style="color:#9aa2b0;font-size:12px;margin-top:16px">Lector XML DIAN de Tributando · ¿Dudas? Responde este correo y te ayudamos.</p>
      </div>
    </div>"""


def _correo_onboarding(paso: int, s) -> tuple[str, str]:
    """(asunto, html) del correo del día `paso` para la suscripción de prueba `s`."""
    if paso == 1:
        cuerpo = f"""
          <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">Tu prueba gratis ya está activa 🎉</h1>
          <p style="font-size:15px;line-height:1.55">Deja de digitar las facturas a mano. Así arrancas en 5 minutos:</p>
          <ol style="font-size:15px;line-height:1.75;padding-left:18px">
            <li><b>Descarga</b> el Lector para Windows.</li>
            <li><b>Entra con tu correo</b> (<b>{s.email}</b>) y el código de 6 dígitos que te llega.</li>
            <li><b>Activa el token de la DIAN</b>, baja las facturas de un cliente y exporta el <b>plano listo</b> para Siigo, World Office o Helisa.</li>
          </ol>
          <p style="font-size:15px">Tienes <b>30 días</b> para probarlo con un cliente real.</p>"""
        return "🚀 Empieza con el Lector en 3 pasos", _wrap_correo(
            "DÍA 1 · CÓMO EMPEZAR", cuerpo, "⬇ Descargar el Lector", _DESCARGA_LECTOR)

    if paso == 7:
        cuerpo = """
          <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">De horas digitando a minutos</h1>
          <p style="font-size:15px;line-height:1.55">Una semana de prueba. Esto es lo que los contadores dejan de hacer a mano:</p>
          <ul style="font-size:15px;line-height:1.75;padding-left:18px">
            <li><b>Cero digitación:</b> baja las facturas de la DIAN y salen en plano para tu software.</li>
            <li><b>Multi-cliente:</b> cada empresa con su carpeta y su historial.</li>
            <li><b>Auditoría de acuses:</b> ve qué compras a crédito faltan por acusar (o pierdes el IVA descontable).</li>
            <li><b>Integra</b> Siigo, World Office y Helisa.</li>
          </ul>
          <!-- TODO: pegar aquí 3 testimonios REALES de contadores (con nombre y ciudad). -->
          <p style="font-size:15px">¿No lo has probado con un cliente todavía? Es el mejor momento.</p>"""
        return "⏱️ Lo que hacen los contadores con el Lector", _wrap_correo(
            "DÍA 7 · CÓMO SE USA", cuerpo, "Abrir el Lector", _DESCARGA_LECTOR)

    if paso == 20:
        rest = (s.vence - date.today()).days if s.vence else 10
        cuerpo = f"""
          <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">Te quedan {rest} días de prueba</h1>
          <p style="font-size:15px;line-height:1.55">Si ya viste cuánto tiempo te ahorra en <b>un</b> cliente, imagina con <b>toda tu cartera</b>, mes a mes.</p>
          <p style="font-size:15px;line-height:1.55">Cuando venza la prueba, tus clientes y su historial quedan guardados — solo activas un plan para seguir.</p>"""
        return f"⏳ Te quedan {rest} días de prueba del Lector", _wrap_correo(
            f"DÍA 20 · QUEDAN {rest} DÍAS", cuerpo, "Ver los planes", _PLANES_LECTOR)

    # paso 28 — cierre
    rest = (s.vence - date.today()).days if s.vence else 2
    rest = max(rest, 0)
    cuerpo = f"""
      <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">Última llamada ⏰</h1>
      <p style="font-size:15px;line-height:1.55">Tu prueba del Lector vence en <b>{rest} día(s)</b>. Activa tu plan y sigue armando los planos sin digitar nada.</p>
      <p style="font-size:15px;line-height:1.55">Tus clientes actuales y su historial <b>no se pierden</b>: al activar el plan, sigues justo donde quedaste.</p>"""
    return "🔔 Tu prueba del Lector vence pronto", _wrap_correo(
        "DÍA 28 · ACTIVA TU PLAN", cuerpo, "Activar mi plan", _PLANES_LECTOR)


def onboarding_lector() -> int:
    """Secuencia de bienvenida a las pruebas gratis del Lector (días 1/7/20/28).

    Envía como máximo UN correo por contador por corrida, en su día (con ventana
    de 3 días por si el job no corrió). Corre a diario junto al informe. Nunca
    escribe a quien ya tiene un plan pagado activo. Devuelve cuántos envió.
    """
    from src.correo import enviar_email

    # `creado` se guarda en UTC (datetime.utcnow); comparo en la misma base.
    hoy_utc = datetime.utcnow().date()
    # Correos con plan pagado activo → no molestar con el onboarding de prueba.
    pagados = {r.email for r in SuscripcionLector.query
               .filter(SuscripcionLector.plan != "prueba",
                       SuscripcionLector.activa.is_(True)).all() if r.email}

    enviados = 0
    for s in SuscripcionLector.query.filter_by(plan="prueba").all():
        if not s.email or not s.creado or s.email in pagados:
            continue
        dias = (hoy_utc - s.creado.date()).days
        hechos = {h for h in (s.onboarding or "").split(",") if h}
        for paso in _ONBOARDING_PASOS:
            # Ventana [paso, paso+2]: tolera días sin correr y evita spamear
            # pruebas viejas al desplegar (una vieja no cae en ninguna ventana).
            if paso <= dias <= paso + 2 and str(paso) not in hechos:
                asunto, html = _correo_onboarding(paso, s)
                try:
                    enviar_email(s.email, asunto, html)
                except Exception:
                    break                      # falló el envío: reintenta mañana
                s.onboarding = ",".join(sorted(hechos | {str(paso)}))
                enviados += 1
                break                          # un solo correo por contador/corrida
    if enviados:
        db.session.commit()
    return enviados


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
    "ESTILO: directo y concreto, cero relleno. PROHIBIDO abrir con frases tipo "
    "'esperamos que este correo te encuentre bien' o 'querido contador'; entra de una "
    "con el dato o el dolor (horas digitando, sanciones, plazos). Cifras con separador "
    "de miles. Sin markdown decorativo excesivo; numera las secciones tal cual."
)


def contenido_semanal(ia_cfg: dict) -> bool:
    """Genera el lote de marketing con Gemini y lo envía por correo. True si salió."""
    from src.asistente import responder
    from src.correo import enviar_email

    fecha = date.today().strftime("%d de %B de %Y")
    brief = _PROMPT_MARKETING.format(fecha=fecha)
    # El brief va como instrucción de sistema (no se recorta a 2.000 chars) y con
    # techo de tokens amplio: el lote trae 5 piezas.
    texto = responder([{"rol": "user", "texto": "Genera el lote de contenido de esta semana."}],
                      ia_cfg, system_extra="\n\n" + brief, max_tokens=2500)
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
