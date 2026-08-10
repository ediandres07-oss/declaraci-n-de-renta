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
    """Correos del dueño / de prueba / QA: nunca deben recibir campañas."""
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return True
    if e.startswith("ediandres07") or e in ("contacto@tributando.co", "davinchitose@gmail.com"):
        return True   # cuentas del dueño (davinchitose = misma cédula que ediandres07)
    if e.split("@")[-1] in ("test.co", "test.com", "example.com", "qa.co"):
        return True
    if e.startswith(("prueba.qa", "nuevo.qa", "qa.", "test.")) or ".qa@" in e:
        return True
    return False


def _emails_contadores() -> set:
    """Correos que son de CONTADORES: suscriptores del Lector, con pase de
    temporada, o que usaron la muestra profesional."""
    from src.auth import AccesoAutorizado, MuestraContador, MuestraContadorEmail
    cont = set()
    for s in SuscripcionLector.query.all():
        if s.email:
            cont.add(s.email.strip().lower())
    for a in AccesoAutorizado.query.all():
        if a.email:
            cont.add(a.email.strip().lower())
    for m in MuestraContador.query.all():
        if m.email:
            cont.add(m.email.strip().lower())
    for m in MuestraContadorEmail.query.all():
        if m.email:
            cont.add(m.email.strip().lower())
    for o in OrdenRegistro.query.all():
        try:
            d = json.loads(o.data)
        except Exception:
            continue
        if d.get("plan") == "contadores":
            c = (d.get("contacto", {}) or {}).get("email") or d.get("email")
            if c:
                cont.add(str(c).strip().lower())
    return cont


def destinatarios_campana(publico: str = "personas", emails=None) -> list[str]:
    """Correos únicos para una campaña, ya segmentados y sin duplicar:
      - 'personas'  → personas naturales (usuarios registrados MENOS contadores)
      - 'contadores'→ suscriptores Lector + pases + muestras profesionales
      - 'guia'      → leads de la guía-obsequio (lista de espera)
      - 'calculo'   → leads del cálculo gratis (dejaron correo en el resultado)
      - 'todos'     → la unión de todo
      - lista a la medida → pasa `emails` y se usa esa lista
    Siempre excluye los correos propios/de prueba."""
    correos = set()
    if emails is not None:
        for e in emails:
            if e:
                correos.add(str(e).strip().lower())
    elif publico == "contadores":
        correos = _emails_contadores()
    elif publico == "cortesia":
        from src.auth import MuestraContador, MuestraContadorEmail
        for m in MuestraContador.query.all():
            if m.email:
                correos.add(m.email.strip().lower())
        for m in MuestraContadorEmail.query.all():
            if m.email:
                correos.add(m.email.strip().lower())
    elif publico == "guia":
        for l in LeadEspera.query.all():
            if l.email:
                correos.add(l.email.strip().lower())
    elif publico == "calculo":
        from src.auth import LeadExogena
        for l in LeadExogena.query.all():
            if l.email:
                correos.add(l.email.strip().lower())
    elif publico == "todos":
        cont = _emails_contadores()
        from src.auth import LeadExogena
        for u in Usuario.query.all():
            if u.email:
                correos.add(u.email.strip().lower())
        correos |= cont
        for l in LeadEspera.query.all():
            if l.email:
                correos.add(l.email.strip().lower())
        for l in LeadExogena.query.all():
            if l.email:
                correos.add(l.email.strip().lower())
    else:                                   # 'personas' = naturales (sin contadores)
        cont = _emails_contadores()
        for u in Usuario.query.all():
            if u.email:
                e = u.email.strip().lower()
                if e not in cont:
                    correos.add(e)
    return sorted(c for c in correos if not _es_propio(c))


def _telegram_enviar(texto: str) -> bool:
    """Manda un mensaje a Telegram. Requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
    en el entorno (Render). Devuelve True si se envió. Nunca lanza."""
    import os
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return False
    try:
        import requests
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                "disable_web_page_preview": True},
                          timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def telegram_configurado() -> bool:
    import os
    return bool((os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
                and (os.environ.get("TELEGRAM_CHAT_ID") or "").strip())


def notificar_venta_telegram(orden_id: str, orden: dict) -> bool:
    """Push a Telegram cuando entra una venta confirmada."""
    plan = orden.get("plan") or "?"
    valor = float(orden.get("precio") or orden.get("valor") or 0)
    contacto = orden.get("contacto") or {}
    quien = contacto.get("nombre") or contacto.get("email") or orden.get("email") or "—"
    nombres = {"pdf": "Formulario 210 (PDF)", "presentacion": "Declaración presentada",
               "contadores": "Pase de temporada", "lector": "Suscripción Lector"}
    valor_txt = f"${valor:,.0f}".replace(",", ".")
    texto = (f"💰 <b>Nueva venta</b>\n{nombres.get(plan, plan)} · <b>{valor_txt}</b>\n"
             f"👤 {quien}\n🧾 Orden {orden_id}")
    return _telegram_enviar(texto)


def metricas_contactos() -> dict:
    """Conteo de contactos por audiencia, para estadísticas y campañas."""
    from src.auth import MuestraContador, MuestraContadorEmail
    cortesia = set()
    for m in MuestraContador.query.all():
        if m.email:
            cortesia.add(m.email.strip().lower())
    for m in MuestraContadorEmail.query.all():
        if m.email:
            cortesia.add(m.email.strip().lower())
    lector = {s.email.strip().lower() for s in SuscripcionLector.query.all() if s.email}
    return {
        "todos": len(destinatarios_campana("todos")),
        "personas": len(destinatarios_campana("personas")),   # naturales registrados
        "calculo": len(destinatarios_campana("calculo")),      # dejaron correo en el cálculo
        "guia": len(destinatarios_campana("guia")),            # lista de espera (guía)
        "contadores": len(destinatarios_campana("contadores")),
        "cortesia": len([c for c in cortesia if not _es_propio(c)]),
        "lector": len([c for c in lector if not _es_propio(c)]),
    }


def envolver_campana(cuerpo_html: str, cta_txt: str = "", cta_url: str = "https://tributando.co") -> str:
    """Envuelve el mensaje de una campaña en la plantilla de marca de Tributando."""
    cta = ""
    if cta_txt:
        cta = (f'<div style="text-align:center;margin:20px 0 6px">'
               f'<a href="{cta_url}" style="display:inline-block;background:#c8991f;color:#fff;'
               f'font-weight:800;text-decoration:none;padding:13px 30px;border-radius:11px">{cta_txt}</a></div>')
    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:540px;margin:auto;color:#2b3242">
      <div style="background:#1e2432;padding:22px 26px;border-radius:14px 14px 0 0;text-align:center">
        <div style="font-size:20px;font-weight:800;color:#fff">Tributando<span style="color:#cdab7e">.co</span></div>
      </div>
      <div style="background:#fff;border:1px solid #e6e2d8;border-top:none;border-radius:0 0 14px 14px;padding:26px;font-size:15px;line-height:1.6">
        {cuerpo_html}
        {cta}
        <p style="color:#9aa2b0;font-size:12px;margin-top:16px">Tributando.co · ¿Dudas? Responde este correo.</p>
      </div>
    </div>"""


def registrar_campana(asunto, publico, total, enviados, fallidos, muestra) -> None:
    from src.auth import CampanaEnviada
    db.session.add(CampanaEnviada(
        asunto=(asunto or "")[:200], publico=(publico or "")[:30], total=total,
        enviados=enviados, fallidos=fallidos, muestra=", ".join(muestra[:5])))
    db.session.commit()


def historial_campanas(limite: int = 40):
    from src.auth import CampanaEnviada
    return CampanaEnviada.query.order_by(CampanaEnviada.fecha.desc()).limit(limite).all()


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


def _atribucion_semana() -> list[str]:
    """Líneas '<canal>: N llegada(s)' de los últimos 7 días: códigos de muestra
    pedidos, muestras descargadas y leads del cálculo gratis, por origen."""
    from src.auth import CodigoMuestra, LeadExogena, MuestraContadorEmail
    desde = datetime.utcnow() - timedelta(days=7)
    conteo = {}

    def _suma(origen, que):
        canal = origen or "directo"
        conteo.setdefault(canal, {}).setdefault(que, 0)
        conteo[canal][que] += 1

    for c in CodigoMuestra.query.all():
        if c.expira and c.expira >= desde and not _es_propio(c.email):
            _suma(c.origen, "códigos")
    for m in MuestraContadorEmail.query.all():
        if m.creado and m.creado >= desde and not _es_propio(m.email):
            _suma(m.origen, "muestras")
    for l in LeadExogena.query.all():
        if l.creado and l.creado >= desde and not _es_propio(l.email):
            _suma(l.origen, "leads renta")

    etiqueta = {"ads": "🎯 Google Ads", "instagram": "📸 Instagram/Meta",
                "youtube": "▶️ YouTube", "whatsapp": "💬 WhatsApp",
                "google_organico": "🔎 Google orgánico", "directo": "🚪 Directo/otro"}
    lineas = []
    for canal, partes in sorted(conteo.items(), key=lambda kv: -sum(kv[1].values())):
        det = " · ".join(f"{n} {q}" for q, n in partes.items())
        lineas.append(f"{etiqueta.get(canal, canal)}: {det}")
    return lineas


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
      <h3 style="margin-bottom:4px">📣 De dónde llegan (últimos 7 días)</h3>
      <ul style="margin-top:4px">{_lista(_atribucion_semana(), "Sin llegadas con origen registrado aún.")}</ul>
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


# ---------- recuperación de leads del cálculo gratis de renta ----------

_MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d) -> str:
    return f"{d.day} de {_MESES[d.month]} de {d.year}" if d else "por confirmar"


def _correo_recuperacion(paso: str, lead) -> tuple[str, str]:
    """(asunto, html) del correo de recuperación `paso` para el lead."""
    nom = (lead.nombre or "").split()[0].title() if lead.nombre else ""
    hola = f"Hola {nom} 👋" if nom else "Hola 👋"
    fecha = _fecha_larga(lead.fecha_limite)
    valor = f"${(lead.valor or 0):,.0f}".replace(",", ".")
    faltan = (lead.fecha_limite - date.today()).days if lead.fecha_limite else None
    servicio = ("Un <b>contador profesional</b> elabora tu declaración, la <b>presenta ante la "
                "DIAN</b> y la deja <b>en firme</b>. Tú no haces nada.")

    if paso == "d1":
        cuerpo = f"""
          <h1 style="font-size:20px;color:#1e2432;margin:0 0 8px">{hola}</h1>
          <p style="font-size:15px;line-height:1.55">Guardamos tu resultado de renta. Tu <b>fecha límite es el {fecha}</b> y tu valor estimado a pagar es <b>{valor}</b>.</p>
          <p style="font-size:15px;line-height:1.55">Cuando quieras, generamos tu <b>Formulario 210</b> listo — o lo <b>presentamos por ti</b>.</p>"""
        return "📄 Guardamos tu resultado de renta", _wrap_correo(
            "TU DECLARACIÓN", cuerpo, "Ver mi declaración →", SITIO)

    if paso == "d3":
        cuerpo = f"""
          <h1 style="font-size:20px;color:#1e2432;margin:0 0 8px">{hola} ¿Prefieres que lo hagamos por ti?</h1>
          <p style="font-size:15px;line-height:1.55">{servicio}</p>
          <p style="font-size:15px;line-height:1.55">Tu plazo vence el <b>{fecha}</b>. Deja tu declaración en firme y olvídate del tema.</p>"""
        return "🧾 Un profesional presenta tu declaración ante la DIAN", _wrap_correo(
            "LO HACEMOS POR TI", cuerpo, "Quiero que la presenten →", SITIO)

    if paso == "venc10":
        cuerpo = f"""
          <h1 style="font-size:20px;color:#1e2432;margin:0 0 8px">{hola} te quedan {faltan} días</h1>
          <p style="font-size:15px;line-height:1.55">Tu <b>fecha límite para declarar renta es el {fecha}</b>. Después, la DIAN cobra <b>sanción por extemporaneidad</b> (mínimo ~$400.000).</p>
          <p style="font-size:15px;line-height:1.55">{servicio}</p>"""
        return f"⏳ Te quedan {faltan} días para declarar renta", _wrap_correo(
            f"QUEDAN {faltan} DÍAS", cuerpo, "Declarar ahora →", SITIO)

    # venc3 — última llamada
    cuerpo = f"""
      <h1 style="font-size:20px;color:#1e2432;margin:0 0 8px">⏰ Últimos días, {nom or ''}</h1>
      <p style="font-size:15px;line-height:1.55">Tu plazo para declarar renta vence el <b>{fecha}</b> ({faltan} día(s)). No dejes que la DIAN te cobre <b>sanción + intereses</b>.</p>
      <p style="font-size:15px;line-height:1.55">{servicio} Alcanzamos a dejarla presentada a tiempo.</p>"""
    return "🔔 Últimos días para declarar tu renta (evita la sanción)", _wrap_correo(
        "ÚLTIMA LLAMADA", cuerpo, "Presentar mi declaración →", SITIO)


def recuperacion_leads() -> int:
    """Secuencia de recuperación de quienes calcularon gratis y no compraron:
    día 1 (refuerzo), día 3 (servicio), y avisos ~10 y ~3 días antes de su fecha
    límite. Un correo por lead por corrida; no escribe a quien ya pagó.
    """
    from src.correo import enviar_email
    from src.auth import LeadExogena

    hoy_utc = datetime.utcnow().date()
    hoy = date.today()
    pagaron = set()
    for o in OrdenRegistro.query.all():
        try:
            d = json.loads(o.data)
        except Exception:
            continue
        if d.get("estado") == "pagada" and d.get("email"):
            pagaron.add(str(d["email"]).strip().lower())

    prioridad = {"venc3": 0, "venc10": 1, "d1": 2, "d3": 3}
    enviados = 0
    for lead in LeadExogena.query.all():
        if not lead.email or not lead.creado or lead.email in pagaron:
            continue
        dias = (hoy_utc - lead.creado.date()).days
        hechos = {h for h in (lead.onboarding or "").split(",") if h}
        pendientes = []
        if 1 <= dias <= 3 and "d1" not in hechos:
            pendientes.append("d1")
        if 3 <= dias <= 5 and "d3" not in hechos:
            pendientes.append("d3")
        if lead.obligado and lead.fecha_limite:
            faltan = (lead.fecha_limite - hoy).days
            if 8 <= faltan <= 12 and "venc10" not in hechos:
                pendientes.append("venc10")
            if 1 <= faltan <= 4 and "venc3" not in hechos:
                pendientes.append("venc3")
        if not pendientes:
            continue
        paso = sorted(pendientes, key=lambda p: prioridad[p])[0]
        asunto, html = _correo_recuperacion(paso, lead)
        try:
            enviar_email(lead.email, asunto, html)
        except Exception:
            continue
        lead.onboarding = ",".join(sorted(hechos | {paso}))
        enviados += 1
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


# ---------- seguimiento comercial: embudo de contadores (muestra → pase) ----------
# Cada día arma los correos de seguimiento para los contadores que pidieron o
# descargaron la muestra y no han comprado. NO envía nada al lead: le manda al
# dueño un resumen con cada correo propuesto y un botón "Aprobar y enviar".
# Tope: máximo 2 seguimientos por lead, y solo dentro de la ventana de cada paso.

_SEG_PASOS = {"codigo": "Pidió el código y no descargó",
              "valor": "Descargó la muestra y no ha comprado",
              "cierre": "Último recordatorio (temporada de renta)"}


def _correo_seguimiento(paso: str, nombre: str = "") -> tuple[str, str]:
    """(asunto, html) del correo de seguimiento `paso` para un lead contador."""
    hola = f"Hola {nombre.split()[0].title()}," if (nombre or "").strip() else "Hola,"
    url = "https://tributando.co/contadores"
    if paso == "codigo":
        asunto = "¿Pudiste descargar tu muestra? — Tributando.co"
        cuerpo = f"""
          <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">{hola}</h1>
          <p style="font-size:15px;line-height:1.7">Vimos que pediste el código para la
          <b>declaración de muestra gratis</b> pero no alcanzaste a descargarla. El código
          vence en 15 minutos, así que a veces se queda a mitad de camino.</p>
          <p style="font-size:15px;line-height:1.7">Pide uno nuevo cuando quieras: entras,
          lo escribes y descargas tu <b>Formulario 210 de muestra con papeles de trabajo</b>
          en un par de minutos.</p>"""
        return asunto, _wrap_correo("MUESTRA GRATIS", cuerpo, "Descargar mi muestra", url)
    if paso == "valor":
        asunto = "Lo que viste en la muestra, para todos tus clientes"
        cuerpo = f"""
          <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">{hola}</h1>
          <p style="font-size:15px;line-height:1.7">Esa declaración de muestra que descargaste
          —210 armado + papeles de trabajo desde la exógena— la puedes tener para
          <b>todos tus clientes, ilimitada</b>, con el <b>Pase de temporada</b>.</p>
          <ul style="font-size:15px;line-height:1.75;padding-left:18px">
            <li>Declaraciones ilimitadas toda la temporada de renta (ago–oct).</li>
            <li>Formulario 210 en PDF + papeles de trabajo en Excel.</li>
            <li>Un solo pago de <b>$149.900</b> — se paga solo con la primera declaración.</li>
          </ul>"""
        return asunto, _wrap_correo("PASE DE TEMPORADA", cuerpo, "Activar mi pase", url)
    # cierre
    asunto = "Ya arrancaron los vencimientos de renta — último recordatorio"
    cuerpo = f"""
      <h1 style="font-size:20px;margin:14px 0 8px;color:#1e2432">{hola}</h1>
      <p style="font-size:15px;line-height:1.7">Los plazos de renta de personas naturales
      (AG 2025) ya están corriendo, según los dos últimos dígitos de la cédula. Es el
      momento en que cada declaración manual te cuesta más horas.</p>
      <p style="font-size:15px;line-height:1.7">Con el <b>Pase de temporada</b> ($149.900,
      pago único) armas declaraciones ilimitadas desde la exógena: 210 en PDF + papeles de
      trabajo. Este es el último correo que te enviamos al respecto. 🙂</p>"""
    return asunto, _wrap_correo("TEMPORADA DE RENTA", cuerpo, "Activar mi pase", url)


def _seg_compradores() -> set:
    """Correos que ya son clientes: pase activo, orden pagada o licencia del Lector."""
    from src.auth import AccesoAutorizado, SuscripcionLector
    out = set()
    try:
        out |= {(a.email or "").lower() for a in AccesoAutorizado.query.all()}
    except Exception:
        pass
    for o in OrdenRegistro.query.all():
        try:
            d = json.loads(o.data)
        except Exception:
            continue
        if d.get("estado") == "pagada" and d.get("email"):
            out.add(str(d["email"]).strip().lower())
    try:
        out |= {(s.email or "").lower() for s in SuscripcionLector.query.all()}
    except Exception:
        pass
    return out


def seguimientos_pendientes() -> list[dict]:
    """Leads del embudo de contadores con un seguimiento listo para aprobar.
    [{email, nombre, paso, motivo}] — máx. 1 paso por lead por corrida."""
    from src.auth import CodigoMuestra, MuestraContador, MuestraContadorEmail, SeguimientoContador

    hoy = datetime.utcnow()
    compradores = _seg_compradores()

    # descargaron la muestra (con o sin registro)
    descargaron = {}
    origenes = {}
    for m in MuestraContadorEmail.query.all():
        if m.email:
            descargaron[m.email.lower()] = (m.creado or hoy, m.nombre or "")
            origenes[m.email.lower()] = m.origen or ""
    for m in MuestraContador.query.all():
        if m.email:
            descargaron.setdefault(m.email.lower(), (getattr(m, "creado", None) or hoy, ""))

    # pidieron código pero nunca descargaron (fecha ≈ expira - 15 min)
    pidieron = {}
    for c in CodigoMuestra.query.all():
        e = (c.email or "").lower()
        if e and e not in descargaron and c.expira:
            pidieron[e] = c.expira - timedelta(minutes=15)
            origenes.setdefault(e, c.origen or "")

    ya = {s.email: {p for p in (s.enviados or "").split(",") if p}
          for s in SeguimientoContador.query.all()}

    out = []
    for email, fecha in pidieron.items():
        if _es_propio(email) or email in compradores or len(ya.get(email, ())) >= 2:
            continue
        dias = (hoy - fecha).days
        if 1 <= dias <= 4 and "codigo" not in ya.get(email, ()):
            out.append({"email": email, "nombre": "", "paso": "codigo",
                        "origen": origenes.get(email, ""),
                        "motivo": f"pidió código hace {dias} día(s), no descargó"})
    for email, (fecha, nombre) in descargaron.items():
        if _es_propio(email) or email in compradores:
            continue
        hechos = ya.get(email, set())
        if len(hechos) >= 2:
            continue
        dias = (hoy - fecha).days
        if 2 <= dias <= 6 and "valor" not in hechos:
            out.append({"email": email, "nombre": nombre, "paso": "valor",
                        "origen": origenes.get(email, ""),
                        "motivo": f"descargó la muestra hace {dias} día(s), sin compra"})
        elif 7 <= dias <= 14 and "cierre" not in hechos:
            out.append({"email": email, "nombre": nombre, "paso": "cierre",
                        "origen": origenes.get(email, ""),
                        "motivo": f"descargó hace {dias} día(s), sin compra"})
    return out


def seguimiento_contadores() -> int:
    """Correo diario al dueño con los seguimientos propuestos y su botón de
    aprobación. No envía nada a los leads. Devuelve cuántos propuso."""
    from urllib.parse import quote
    from src.correo import enviar_email

    pend = seguimientos_pendientes()
    if not pend:
        return 0
    bloques = []
    for p in pend:
        asunto, _ = _correo_seguimiento(p["paso"], p["nombre"])
        aprobar = (f"https://tributando.co/admin/seguimiento/aprobar"
                   f"?email={quote(p['email'])}&paso={p['paso']}")
        bloques.append(f"""
        <div style="border:1px solid #e2ddd2;border-radius:10px;padding:14px;margin:10px 0">
          <div style="font-weight:800">{p['email']}</div>
          <div style="color:#8a6d3b;font-size:13px">{_SEG_PASOS[p['paso']]} — {p['motivo']}{(' · llegó por ' + p['origen']) if p.get('origen') else ''}</div>
          <div style="font-size:14px;margin:6px 0">Asunto: <i>{asunto}</i></div>
          <a href="{aprobar}" style="display:inline-block;background:#c8991f;color:#fff;font-weight:800;text-decoration:none;padding:9px 18px;border-radius:9px">✅ Aprobar y enviar</a>
        </div>""")
    html = f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;margin:auto;color:#2b3242">
      <h2 style="color:#1e2432">🤝 Seguimiento a contadores — {len(pend)} propuesto(s)</h2>
      <p style="color:#5a6272">Tu agente comercial preparó estos correos. Nada se envía
      sin tu clic. Cada lead recibe máximo 2 seguimientos.</p>
      {''.join(bloques)}
    </div>"""
    enviar_email(ADMIN_EMAIL, f"🤝 {len(pend)} seguimiento(s) comercial(es) por aprobar", html)
    return len(pend)


def seguimiento_aprobar(email: str, paso: str) -> dict:
    """Envía el seguimiento `paso` al lead `email` y lo deja registrado (lo
    dispara el dueño desde el botón del correo resumen)."""
    from src.auth import SeguimientoContador, MuestraContadorEmail
    from src.correo import enviar_email

    email = (email or "").strip().lower()
    if not email or paso not in _SEG_PASOS:
        return {"ok": False, "error": "Datos incompletos."}
    reg = db.session.get(SeguimientoContador, email)
    hechos = {p for p in ((reg.enviados if reg else "") or "").split(",") if p}
    if paso in hechos:
        return {"ok": False, "error": "Ese seguimiento ya se había enviado."}
    if len(hechos) >= 2:
        return {"ok": False, "error": "Ese lead ya recibió sus 2 seguimientos."}
    m = db.session.get(MuestraContadorEmail, email)
    asunto, html = _correo_seguimiento(paso, (m.nombre if m else "") or "")
    enviar_email(email, asunto, html)
    if reg is None:
        reg = SeguimientoContador(email=email)
        db.session.add(reg)
    reg.enviados = ",".join(sorted(hechos | {paso}))
    db.session.commit()
    return {"ok": True, "email": email, "paso": paso}
