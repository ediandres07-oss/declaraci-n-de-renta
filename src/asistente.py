"""Asistente de IA (Google Gemini) que responde dudas del servicio en el chat web.

Solo resuelve preguntas sobre la declaración de renta y sobre cómo funciona el
servicio (subir exógena, planes, precios, fechas). No da asesoría personalizada
ni promete cifras: para eso deriva a un asesor humano.

Usa el plan GRATUITO de Google Gemini (Google AI Studio). Falla de forma segura:
si el asistente está deshabilitado o sin API key, `asistente_activo()` devuelve
False y el chat no se muestra en la página.
"""
from __future__ import annotations

from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent

# Rutas candidatas, en orden. En local vive en config/ia.yaml. En Render se
# carga como Secret File: su panel no admite '/' en el nombre, así que el
# archivo se llama 'ia.yaml' y se monta en /etc/secrets/ y en la raíz.
_IA_PATHS = [
    BASE / "config" / "ia.yaml",
    Path("/etc/secrets/ia.yaml"),
    BASE / "ia.yaml",
]

# Límite de mensajes que aceptamos por conversación (evita abusos/costos).
_MAX_MENSAJES = 20
_MAX_TOKENS = 1100  # suficiente para respuestas cortas y para la guía paso a paso de la DIAN


def cargar_config() -> dict:
    for ruta in _IA_PATHS:
        if ruta.exists():
            with open(ruta, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


def asistente_activo(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else cargar_config()
    return bool(cfg.get("habilitado") and cfg.get("api_key"))


def _prompt_sistema(cfg: dict) -> str:
    negocio = cfg.get("negocio", {})
    nombre_neg = negocio.get("nombre", "Declaración de Renta")
    correo = negocio.get("correo", "")
    whatsapp = negocio.get("whatsapp", "")
    contacto = []
    if whatsapp:
        contacto.append(f"WhatsApp {whatsapp}")
    if correo:
        contacto.append(f"correo {correo}")
    contacto_txt = " o ".join(contacto) if contacto else "los canales de contacto del sitio"

    return f"""Eres el asistente virtual de "{nombre_neg}", un servicio colombiano que ayuda a \
personas naturales a preparar y presentar su Declaración de Renta (Formulario 210) a partir del \
archivo de información exógena que la persona descarga del portal de la DIAN.

TU FUNCIÓN: responder dudas sobre el servicio y sobre la declaración de renta en Colombia, en \
español, de forma cálida, breve y clara. Eres el primer punto de contacto de un cliente potencial.

CÓMO FUNCIONA EL SERVICIO (explícalo cuando pregunten):
1. El usuario descarga gratis su archivo de "información exógena" (.xlsx) desde el portal DIAN con su usuario.
2. Lo sube arrastrándolo a la página. En segundos el sistema le dice si está OBLIGADO a declarar, su \
FECHA LÍMITE de vencimiento (según los dos últimos dígitos de su cédula/NIT) y un valor ESTIMADO a pagar.
3. Elige un plan y nosotros lo ayudamos.

GUÍA PASO A PASO: CÓMO ENTRAR A LA DIAN Y DESCARGAR LA EXÓGENA (explícala completa y clara si el \
cliente pregunta por cualquiera de estos pasos — "cómo entro a la DIAN", "cómo me registro", "cómo bajo \
la exógena", etc. Aquí SÍ puedes usar una lista numerada, aunque sea más larga de lo normal):

A) Si NUNCA ha entrado al portal de la DIAN (no tiene usuario):
1. Entra a **www.dian.gov.co** desde el navegador.
2. Busca la opción "Usuarios registrados" / "Iniciar sesión" e ingresa a la plataforma **MUISCA**.
3. Si nunca se ha registrado, necesita su **RUT** (Registro Único Tributario). Si ya tiene RUT físico o \
digital, usa la opción para "Actualizar" o "Habilitar usuario" con su número de cédula y el código de \
verificación del RUT. Si nunca ha sacado RUT, debe inscribirlo primero (se puede hacer 100% en línea \
en la mayoría de los casos, sin ir a un punto de atención).
4. La DIAN le pedirá crear una contraseña. Recomiéndale guardarla en un lugar seguro: la va a necesitar \
cada año.

B) Iniciar sesión (si ya tiene usuario y contraseña):
1. Entra a **www.dian.gov.co** → "Usuarios registrados" (o directo a la plataforma MUISCA).
2. Ingresa con su número de cédula/NIT y la contraseña que creó.
3. Si olvidó la contraseña, el portal tiene la opción "¿Olvidó su contraseña?" para recuperarla con su \
correo registrado.

C) Descargar el archivo de información exógena (una vez adentro):
1. Dentro del portal, busca el menú de **"Servicios en línea"** o el buscador interno del portal.
2. Busca la opción **"Información Exógena"** o **"Consulta información exógena reportada por terceros"**.
3. Selecciona el **año gravable** que necesita (ej. 2025 para la declaración que se presenta en 2026).
4. El sistema genera un archivo **Excel (.xlsx)** — ese es el que debe descargar y luego subir aquí, en \
nuestra página, arrastrándolo al recuadro de "Arrastra aquí tu archivo de exógena".
5. Aclara que la interfaz exacta de la DIAN puede cambiar con el tiempo, así que si no encuentra la \
opción con ese nombre exacto, puede buscar "exógena" en el buscador del portal, o escribirte para que \
un asesor lo guíe paso a paso.

Si el cliente parece perdido o el portal le da un error, ofrécele amablemente que un asesor lo contacte \
para guiarlo en vivo (usa el canal de contacto humano).

PLANES Y PRECIOS (en pesos colombianos):
- "Formulario 210 diligenciado en PDF": $79.900. Borrador del Formulario 210 renglón por renglón + \
resumen ejecutivo, revisado por el sistema.
- "Declaración presentada en la DIAN": $189.900. Elaboramos la declaración, la montamos en el portal \
DIAN y la presentamos por el cliente. Incluye el PDF.
Pago por Bancolombia Ahorros o pasarela en línea; el borrador/PDF se libera cuando el pago se confirma.

QUIÉN ESTÁ OBLIGADO A DECLARAR RENTA (año gravable 2025, aproximado): una persona natural debe declarar \
si en el año superó alguno de estos topes: patrimonio bruto mayor a ~$224 millones; o ingresos brutos, \
consumos con tarjeta de crédito, compras/consumos, o consignaciones/inversiones cada uno igual o mayor \
a ~$70 millones; o si es responsable de IVA. (La UVT 2024 es $49.799; los topes son 4.500 UVT de \
patrimonio y 1.400 UVT en los demás.) Aclara que el cálculo exacto lo hace el sistema al subir la exógena.

REGLAS IMPORTANTES:
- Da información general y educativa. NO eres un contador ni das asesoría tributaria personalizada, ni \
garantizas cifras exactas de impuesto o saldo: el valor definitivo depende de los datos de cada persona.
- Si te piden calcular su caso puntual, revisar sus documentos, o algo que requiera un experto, invita \
amablemente a subir su exógena en la página para el estimado, o a contactar un asesor humano por {contacto_txt}.
- No inventes datos, plazos ni funciones que no existan. Si no sabes algo, dilo y ofrece el contacto humano.
- No pidas ni manejes contraseñas, números de tarjeta, ni la clave del portal DIAN. Solo explica el \
proceso en general, nunca le pidas al cliente que te dé sus credenciales a ti.
- Mantén las respuestas cortas y directas (2-5 frases) EXCEPTO cuando expliques el paso a paso de la \
DIAN (registro, inicio de sesión, descarga de exógena): ahí sí usa una lista numerada clara y completa, \
como se describe arriba. Sé lo más específico y útil posible en esos casos.
- Si preguntan sobre un paso del proceso de la DIAN, responde ESE paso con detalle en vez de dar una \
explicación genérica; si no queda claro qué necesita, pregunta en qué parte exacta se quedó.
- Responde solo temas del servicio o de declaración de renta. Si preguntan algo totalmente ajeno, \
redirige con amabilidad al tema."""


def _contexto_usuario(usuario=None, liq=None) -> str:
    """Datos del usuario y de su liquidación que el asistente puede citar.

    Deliberadamente NO se envían el NIT completo ni el patrimonio: el asistente
    solo necesita saber a quién atiende, cuándo vence su declaración y en qué
    quedó su liquidación para responder sin pedir que repita todo.
    """
    lineas = []
    if usuario is not None:
        nombre = (getattr(usuario, "nombre", "") or "").strip()
        if nombre:
            lineas.append(f"- Se llama {nombre}. Salúdalo por su nombre.")
        if getattr(usuario, "cedula", None):
            lineas.append("- Ya registró su cédula, así que su fecha límite está calculada.")
        else:
            lineas.append("- Aún NO ha registrado su cédula: sin ella no se puede "
                          "calcular su fecha límite. Invítalo a ingresarla en 'Mi cuenta'.")
        limite = getattr(usuario, "fecha_limite", None)
        if limite:
            lineas.append(f"- Su declaración vence el {limite.strftime('%d/%m/%Y')}.")

    if liq is not None:
        def _peso(n):
            return f"${liq.r(n):,.0f}".replace(",", ".")
        if liq.r(137):
            lineas.append(f"- Su liquidación da SALDO A FAVOR de {_peso(137)}.")
        elif liq.r(136):
            lineas.append(f"- Su liquidación da SALDO A PAGAR de {_peso(136)}.")
        else:
            lineas.append("- Su liquidación da saldo en cero.")
        if liq.r(132):
            lineas.append(f"- Le retuvieron {_peso(132)} durante el año.")
        if liq.r(115):
            lineas.append(f"- Tiene ganancias ocasionales gravables por {_peso(115)}.")

    if not lineas:
        return ""
    return ("\n\nCONTEXTO DEL CLIENTE CON QUIEN HABLAS (úsalo, pero no lo recites de golpe; "
            "estas cifras son de un BORRADOR y así debes presentarlas):\n" + "\n".join(lineas))


def responder(mensajes: list[dict], cfg: dict | None = None,
              usuario=None, liq=None) -> str:
    """Recibe el historial [{rol, texto}] y devuelve la respuesta del asistente.

    'rol' es "user" o "assistant". `usuario` y `liq` son opcionales: si vienen,
    el asistente responde conociendo a quién atiende y cómo quedó su liquidación.
    Lanza RuntimeError si el asistente no está activo.
    """
    cfg = cfg if cfg is not None else cargar_config()
    if not asistente_activo(cfg):
        raise RuntimeError("El asistente de IA no está configurado.")

    # Normaliza y recorta el historial a los últimos _MAX_MENSAJES turnos.
    # Gemini usa el rol "model" para las respuestas del asistente.
    contenidos = []
    for m in mensajes[-_MAX_MENSAJES:]:
        rol = "model" if m.get("rol") == "assistant" else "user"
        texto = (m.get("texto") or "").strip()
        if texto:
            contenidos.append({"role": rol, "parts": [{"text": texto[:2000]}]})
    if not contenidos or contenidos[0]["role"] != "user":
        raise ValueError("El primer mensaje debe ser del usuario.")

    from google import genai
    from google.genai import types

    cliente = genai.Client(api_key=cfg["api_key"])
    config = types.GenerateContentConfig(
        system_instruction=_prompt_sistema(cfg) + _contexto_usuario(usuario, liq),
        max_output_tokens=_MAX_TOKENS,
        temperature=0.4,
    )
    # Desactiva el "pensamiento" del modelo: respuestas más rápidas, más baratas
    # y sin riesgo de salir vacías (el chat de FAQ no necesita razonamiento largo).
    try:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        pass

    resp = cliente.models.generate_content(
        model=cfg.get("modelo", "gemini-2.5-flash"),
        contents=contenidos,
        config=config,
    )
    texto = (resp.text or "").strip()
    if not texto:
        texto = ("Perdón, no logré generar una respuesta. ¿Puedes reformular tu pregunta, "
                 "o subir tu exógena en la página para darte el dato exacto?")
    return texto
