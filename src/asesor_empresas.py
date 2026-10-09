"""Asesor comercial por WhatsApp para las EMPRESAS de la prospección (portafolio).

Cuando una empresa escribe al WhatsApp de Tributando (normalmente con el texto del
botón del portafolio), el asesor:
  1. la identifica en el CRM: por su NIT en el mensaje, por el número ya asociado, o
     porque abrió el portafolio hace pocos minutos (si hay un solo candidato);
  2. responde con lo aprobado (Plan Empresa Nueva, nómina, cómo se arranca) usando
     Claude si hay ANTHROPIC_API_KEY o, si no, el mismo Gemini del chat de la web;
  3. si la persona quiere la propuesta, le manda el enlace de su propuesta
     personalizada (/propuesta) por WhatsApp y por correo, y marca el CRM;
  4. le avisa a Edison (Telegram + correo) del contacto, de la propuesta y de las
     llamadas o temas que el asesor no debe resolver solo.
Lo que el modelo pide hacer va en marcas al final de su texto: [[PROPUESTA]],
[[LLAMADA]] o [[EDISON]]; el código las quita antes de enviar.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta

_log = logging.getLogger(__name__)

_PISTAS = ("portafolio", "propuesta", "mi empresa", "nuestra empresa", "contabilidad para",
           "cotización", "cotizacion", "cuánto cuesta", "cuanto cuesta", "nit")

PROMPT = """Eres el asesor comercial de Tributando por WhatsApp. Atiendes, en nombre de Edison \
Monsalve (Contador Público), a empresas NUEVAS que vieron nuestro portafolio y quieren una propuesta.

Qué ofrecemos (es lo ÚNICO que puedes cotizar):
- PLAN EMPRESA NUEVA: $875.000 al mes (medio salario mínimo). Incluye contabilidad completa del \
mes en nuestra plataforma con las facturas que trae la DIAN, declaraciones de IVA, retención en la \
fuente e ICA, conciliación bancaria y estados financieros; inventario y costos si la empresa lo \
necesita (fabrica, vende productos o hace obras).
- Declaración de renta y exógena del año: por el valor de una mensualidad.
- Montaje inicial incluido: revisión del RUT y las responsabilidades, plan de cuentas, terceros y \
acompañamiento para habilitarse como facturador electrónico ante la DIAN.
- La FACTURA ELECTRÓNICA la emite la empresa (con el facturador gratuito de la DIAN o el de su \
proveedor o tienda en línea); el plan NO incluye emitir sus facturas. Nosotros le acompañamos a \
habilitarse y todas sus facturas entran solas a la contabilidad.
- FACTURA ELECTRÓNICA (opcional, aparte del plan): si todavía no tienen facturador, también se \
lo podemos ofrecer; se cotiza según cuántas facturas emitan al mes. No des precio: si les interesa, \
di que Edison les cotiza y termina con [[EDISON]].
- NÓMINA ELECTRÓNICA (si tienen o van a tener empleados): $25.000 por empleado al mes, mínimo $150.000.
- Crecemos con ellos: a medida que la empresa crece sumamos nómina, punto de venta, costos y \
reportes en la misma plataforma, sin cambiar de contador ni de programa.
- Un contador de verdad: respuesta el mismo día hábil, revisión mensual de la DIAN contra los libros.

Cómo atiendes:
- Mensajes CORTOS, cálidos y claros, como WhatsApp (máximo 5 líneas). Tutea con respeto. Sin tablas.
- Para cotizar necesitas: 1) nombre de la empresa o NIT, 2) a qué se dedica, 3) si tiene o tendrá \
empleados y cuántos. Pide solo lo que falte, de a una o dos preguntas.
- Si en el contexto ya está la empresa identificada, confírmala con la persona («¿es de X?») \
antes de darlo por hecho.
- Cuando la empresa esté confirmada y la persona quiera la propuesta, dile que se la envías y \
termina tu mensaje con la marca [[PROPUESTA]] (el sistema manda el enlace por aquí y por correo).
- Si quiere una llamada o reunión, dile que Edison la llama hoy y termina con [[LLAMADA]].
- NO inventes precios ni servicios, NO des descuentos, NO hables de IVA sobre los honorarios, NO \
asesores impuestos de su caso particular. Si piden algo fuera de esto (empresa grande, varias \
empresas, descuentos, temas tributarios concretos, quejas), di que Edison les responde \
personalmente y termina con [[EDISON]].
- Nunca digas que eres una IA salvo que te lo pregunten; si preguntan, di que eres el asistente \
de Tributando y que Edison revisa cada caso."""


def _modelos():
    from src.prospectos import Prospecto, WaProspecto, db
    return Prospecto, WaProspecto, db


def es_empresa(remitente: str, texto: str) -> bool:
    """¿Esta conversación es de una empresa (asesor) y no de un contador (bot de siempre)?"""
    try:
        _, WaProspecto, db = _modelos()
        if db.session.get(WaProspecto, remitente):
            return True
    except Exception:  # noqa: BLE001
        pass
    t = (texto or "").lower()
    return any(p in t for p in _PISTAS)


def identificar(remitente: str, historial: list):
    """El Prospecto de este número, o None. Asocia el número cuando hay certeza."""
    Prospecto, WaProspecto, db = _modelos()
    wa = db.session.get(WaProspecto, remitente)
    if wa and wa.email:
        return db.session.get(Prospecto, wa.email), True
    texto = " ".join(h["texto"] for h in historial if h.get("rol") == "user")
    # 1) NIT en el mensaje (9 dígitos que empiezan por 9, con o sin puntos)
    for m in re.findall(r"\b9[\d.]{8,11}\b", texto):
        nit = re.sub(r"\D", "", m)[:9]
        p = Prospecto.query.filter(Prospecto.nit.like(f"%{nit}%")).first()
        if p:
            _asociar(remitente, p.email)
            return p, True
    # 2) razón social escrita en el chat
    palabras = [w for w in re.findall(r"[A-ZÁÉÍÓÚÑ0-9&]{4,}", texto.upper()) if w not in ("HOLA", "EDISON", "TRIBUTANDO", "EMPRESA")]
    for w in palabras[:6]:
        cands = Prospecto.query.filter(Prospecto.razon_social.ilike(f"%{w}%")).limit(3).all()
        if len(cands) == 1:
            return cands[0], False
    # 3) abrió el portafolio hace poco y es el único
    hace = datetime.utcnow() - timedelta(minutes=45)
    rec = Prospecto.query.filter(Prospecto.visto_en >= hace).order_by(Prospecto.visto_en.desc()).limit(3).all()
    if len(rec) == 1:
        return rec[0], False
    return None, False


def _asociar(remitente: str, email: str) -> None:
    Prospecto, WaProspecto, db = _modelos()
    wa = db.session.get(WaProspecto, remitente) or WaProspecto(remitente=remitente)
    wa.email = email
    db.session.add(wa)
    db.session.commit()


def _contexto(p, seguro: bool) -> str:
    if not p:
        return "\n\n[Contexto] Todavía no sabemos qué empresa es: pídele el nombre de la empresa o el NIT."
    estado = "IDENTIFICADA (ya confirmada)" if seguro else "PROBABLE (confírmala con la persona)"
    return (f"\n\n[Contexto] Empresa {estado}: {p.razon_social} · NIT {p.nit} · {p.municipio} · "
            f"actividad: {p.actividad} · matrícula {p.fecha_matricula} · etapa CRM: {p.etapa or 'sin respuesta'}"
            f"{' · ya se le envió propuesta' if p.etapa == 'propuesta' else ''}.")


def _llamar_modelo(historial: list, system: str, cfg: dict) -> str:
    clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if clave:
        try:
            import anthropic
            cli = anthropic.Anthropic(api_key=clave)
            msgs = [{"role": "user" if h["rol"] == "user" else "assistant", "content": h["texto"]}
                    for h in historial]
            while msgs and msgs[0]["role"] != "user":
                msgs.pop(0)
            r = cli.messages.create(model=os.environ.get("ASESOR_MODELO", "claude-haiku-4-5-20251001"),
                                    max_tokens=500, system=system, messages=msgs)
            return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
        except Exception:  # noqa: BLE001
            _log.warning("Asesor: falló Claude, sigo con Gemini", exc_info=True)
    from src.asistente import responder
    return responder(historial, cfg, contexto="empresa", system_extra=system[len(PROMPT):], max_tokens=500)


def _avisar(texto: str) -> None:
    try:
        from src import gerente
        gerente._telegram_enviar(texto)
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.correo import enviar_email
        enviar_email("ediandres07@gmail.com", "📲 WhatsApp de una empresa (asesor Tributando)",
                     f"<pre style='font-family:-apple-system,sans-serif;font-size:14px;white-space:pre-wrap'>{texto}</pre>")
    except Exception:  # noqa: BLE001
        pass


def generar(historial: list, remitente: str, cfg: dict) -> str:
    """Respuesta del asesor para este chat (y las acciones que pida)."""
    Prospecto, WaProspecto, db = _modelos()
    p, seguro = identificar(remitente, historial)
    primero = len([h for h in historial if h.get("rol") == "user"]) == 1
    respuesta = _llamar_modelo(historial, PROMPT + _contexto(p, seguro), cfg) or ""
    marcas = set(re.findall(r"\[\[(PROPUESTA|LLAMADA|EDISON)\]\]", respuesta))
    respuesta = re.sub(r"\s*\[\[(PROPUESTA|LLAMADA|EDISON)\]\]\s*", " ", respuesta).strip()
    quien = f"{p.razon_social} (NIT {p.nit}, {p.email})" if p else "empresa sin identificar"
    if primero:
        _avisar(f"Nueva empresa por WhatsApp: +{remitente}\n{quien}\nDijo: {historial[-1]['texto'][:300]}")
    if "PROPUESTA" in marcas and p:
        from src.prospectos import url_propuesta, enviar_propuesta_correo
        _asociar(remitente, p.email)
        respuesta += f"\n\nAquí está tu propuesta: {url_propuesta(p.email)}\nTambién te la mandé a {p.email}."
        enviado = enviar_propuesta_correo(p)
        p.etapa = "propuesta"
        p.nota = ((p.nota or "") + f" · Propuesta por WhatsApp +{remitente} {datetime.utcnow():%d/%m}")[:500]
        wa = db.session.get(WaProspecto, remitente)
        if wa:
            wa.propuesta_en = datetime.utcnow()
        db.session.commit()
        _avisar(f"✅ Propuesta enviada a {quien} por WhatsApp (+{remitente})" + ("" if enviado else " · el correo NO salió"))
    elif "PROPUESTA" in marcas:
        respuesta += "\n\nPara enviártela necesito el NIT o el nombre exacto de la empresa 🙏"
    if marcas & {"LLAMADA", "EDISON"}:
        _avisar(f"📞 {'Pide llamada' if 'LLAMADA' in marcas else 'Tema para ti'}: +{remitente} · {quien}\n"
                f"Último mensaje: {historial[-1]['texto'][:300]}\nChat: https://wa.me/{remitente}")
    return respuesta
