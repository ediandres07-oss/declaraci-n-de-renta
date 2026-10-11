"""Borrador de página web para las empresas de la prospección.

Cada empresa ve SU página armada con los datos del RUES (nombre, actividad,
ciudad y correo) en un enlace privado y firmado: /sitio?e=<correo>&t=<firma>.
No se indexa (noindex) y va marcada como borrador de Tributando; si la quieren,
se publica con su dominio por un valor aparte de la contabilidad.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from urllib.parse import quote

PRECIO_DISENO = 700000        # diseño y publicación, una vez (Edison 10-oct-2026)
PRECIO_ANUAL = 150000         # dominio .com.co + hosting, al año


def token_sitio(email: str) -> str:
    from src.prospectos import _secreto
    return hmac.new(_secreto(), ("sitio|" + email.lower().strip()).encode(),
                    hashlib.sha256).hexdigest()[:20]


def url_sitio(email: str) -> str:
    from src.prospectos import URL
    return f"{URL}/sitio?e={quote(email)}&t={token_sitio(email)}"


_SUFIJOS = r"\b(S\.?\s?A\.?\s?S\.?|SAS|S\.?\s?A\.?|LTDA\.?|E\.?\s?U\.?|BIC|ZOMAC)\b\.?"


def marca(razon_social: str) -> str:
    """«BIOTECH PET S.A.S» → «BIOTECH PET» (sin la forma societaria)."""
    # Solo al FINAL del nombre: «EU CONTAINER S.A.S» no pierde el «EU».
    m = (razon_social or "").strip()
    while True:
        n = re.sub(r"[\s.,-]*" + _SUFIJOS + r"[\s.,-]*$", "", m, flags=re.I)
        if n == m:
            break
        m = n
    return re.sub(r"\s{2,}", " ", m).strip(" .,-") or (razon_social or "Su empresa")


def ciudad(municipio: str) -> str:
    c = (municipio or "").strip().title()
    return {"Medellin": "Medellín", "Itagui": "Itagüí", "Bogota": "Bogotá"}.get(c, c) or "Colombia"


def _u(foto: str, w: int = 1200) -> str:
    """Foto de Unsplash (licencia libre, uso comercial permitido)."""
    return f"https://images.unsplash.com/{foto}?auto=format&fit=crop&w={w}&q=70"


# Pasos de «cómo trabajamos» según vendan productos o servicios.
_PASOS = {
    "productos": [("Escríbanos", "Cuéntenos qué necesita usted o su negocio."),
                  ("Reciba catálogo y precios", "Le enviamos referencias, presentaciones y valores."),
                  ("Le llevamos su pedido", "Entregas en {ciudad} y el área metropolitana.")],
    "servicios": [("Cuéntenos su necesidad", "Una primera conversación, sin costo ni compromiso."),
                  ("Reciba la propuesta", "Alcance, tiempos y valor claros y por escrito."),
                  ("Cumplimos", "Hacemos lo acordado y le mantenemos informado.")],
}

# (claves en la actividad, perfil). El primero que coincide manda; el último es el general.
# fotos: portada, nosotros, banda final; servicios: (foto, título, texto).
_PERFILES = [
    (("limpieza", "aseo", "fumig", "lavander"), dict(
        color="#0369A1", tipo="servicios", ceja="Aseo y desinfección",
        hero="Espacios limpios y seguros en {ciudad}",
        sub="Limpieza, desinfección y mantenimiento para hogares, oficinas e industrias.",
        f_hero="photo-1581578731548-c64695cc6952", f_nos="photo-1686178827149-6d55c72d81df",
        sec="Limpieza profesional para cada espacio",
        servicios=[("photo-1627905646269-7f034dcc5738", "Empresas y oficinas", "Planes de aseo y mantenimiento periódico."),
                   ("photo-1740657254989-42fe9c3b8cce", "Hogares", "Limpieza profunda y de rutina."),
                   ("photo-1628177142898-93e36e4e3a50", "Desinfección", "Protocolos para áreas de alto tráfico.")],
        nos="Una empresa de {ciudad} que cuida sus espacios",
        f_banda="photo-1761558794306-466448dab4bc", banda="¿Necesita un plan de aseo?",
        banda_txt="Visitamos el lugar y le damos la cotización por escrito, sin costo.", banda_btn="Pedir cotización")),
    (("veterin",), dict(
        color="#0E7490", tipo="servicios", ceja="Veterinaria",
        hero="Cuidamos a su mascota como parte de la familia",
        sub="Consulta, vacunación y atención veterinaria con calidez y experiencia en {ciudad}.",
        f_hero="photo-1700665537604-412e89a285c3", f_nos="photo-1700665537650-1bf37979aae0",
        sec="Atención veterinaria completa",
        servicios=[("photo-1644675272883-0c4d582528d8", "Consulta", "Revisión completa y orientación para su perro o gato."),
                   ("photo-1725409796872-8b41e8eca929", "Vacunación y control", "Planes de vacunas y desparasitación al día."),
                   ("photo-1551076805-e1869033e561", "Procedimientos", "Instalaciones equipadas para procedimientos y cirugías.")],
        nos="Un equipo veterinario en {ciudad}",
        f_banda="photo-1553688738-a278b9f063e0", banda="¿Su mascota necesita una cita?",
        banda_txt="Escríbanos y le asignamos el primer espacio disponible.", banda_btn="Agendar cita")),
    (("inmobiliari", "bienes raíces", "finca raíz", "propiedad horizontal"), dict(
        color="#1E3A8A", tipo="servicios", ceja="Inmobiliaria",
        hero="Su próximo hogar o inversión en {ciudad}",
        sub="Arriendo, venta y administración de inmuebles con acompañamiento de principio a fin.",
        f_hero="photo-1624204386084-dd8c05e32226", f_nos="photo-1626178793926-22b28830aa30",
        sec="Inmuebles con respaldo",
        servicios=[("photo-1628592102751-ba83b0314276", "Arriendo", "Apartamentos, casas y locales listos para habitar."),
                   ("photo-1741156386380-0236c72eb6f9", "Venta", "Le ayudamos a comprar o vender al precio justo."),
                   ("photo-1728825445493-1a6e89164511", "Administración", "Cuidamos su inmueble y le rendimos cuentas cada mes.")],
        nos="Una inmobiliaria de {ciudad} de confianza",
        f_banda="photo-1665249934445-1de680641f50", banda="¿Tiene un inmueble para arrendar o vender?",
        banda_txt="Lo visitamos, le damos un valor de referencia y nos encargamos del resto.", banda_btn="Hablar con un asesor")),
    (("software", "sistemas inform", "datos", "telecomunic", "programaci", "hosting", "tecnolog", "digital", "internet", "informátic", "portales web", "desarrollo experimental"), dict(
        color="#6D28D9", tipo="servicios", ceja="Tecnología",
        hero="Tecnología que hace crecer su negocio",
        sub="Soluciones digitales a la medida, con soporte cercano desde {ciudad}.",
        f_hero="photo-1522071820081-009f0129c71c", f_nos="photo-1517048676732-d65bc937f952",
        sec="Soluciones digitales para su empresa",
        servicios=[("photo-1555066931-4365d14bab8c", "Desarrollo a la medida", "Software y aplicaciones para su operación."),
                   ("photo-1558494949-ef010cbdcc31", "Infraestructura y nube", "Su información segura y siempre disponible."),
                   ("photo-1626863905121-3b0c0ed7b94c", "Soporte", "Acompañamiento cuando lo necesita.")],
        nos="Un equipo de tecnología en {ciudad}",
        f_banda="photo-1541746972996-4e0b0f43e02a", banda="¿Tiene un proceso que quiere automatizar?",
        banda_txt="Lo revisamos con usted y le proponemos la solución.", banda_btn="Agendar reunión")),
    (("alojamiento", "hotel", "turístic", "turistic", "viaje", "reserva", "convenciones", "eventos"), dict(
        color="#9A3412", tipo="servicios", ceja="Turismo y eventos",
        hero="Viva {ciudad} y Antioquia con nosotros",
        sub="Alojamiento, planes y eventos con atención cálida y todos los detalles resueltos.",
        f_hero="photo-1737914692031-4cd02e08dffd", f_nos="photo-1621293954908-907159247fc8",
        sec="Experiencias bien atendidas",
        servicios=[("photo-1582719478250-c89cae4dc85b", "Alojamiento", "Espacios cómodos, limpios y bien ubicados."),
                   ("photo-1570197820006-3e46f5ee01bb", "Planes y tours", "Recorridos por los pueblos y paisajes de Antioquia."),
                   ("photo-1540575467063-178a50c2df87", "Eventos", "Convenciones, reuniones y celebraciones organizadas.")],
        nos="Una empresa de {ciudad} que recibe bien",
        f_banda="photo-1596436889106-be35e843f974", banda="¿Planea un viaje o un evento?",
        banda_txt="Cuéntenos fechas y número de personas, y le enviamos la propuesta.", banda_btn="Pedir cotización")),
    (("animal", "mascota", "veterin"), dict(
        color="#B4501A", tipo="productos", ceja="Alimento para mascotas",
        hero="Nutrición de verdad para perros y gatos",
        sub="Productos pensados para el bienestar de las mascotas, para que coman bien todos los días.",
        f_hero="photo-1522276498395-f4f68f7f8454", f_nos="photo-1692906456160-385d805be646",
        sec="Alimento para cada mascota y cada etapa",
        servicios=[("photo-1714068691210-073dc52c6c1d", "Para perros", "Cachorros, adultos y senior. Presentaciones para el hogar y para criaderos."),
                   ("photo-1645773619957-ec1d128d0aa2", "Para gatos", "Pensado para el gusto y las necesidades de los gatos de la casa."),
                   ("photo-1568640347023-a616a30bc3bd", "Snacks y premios", "Para entrenar, consentir y premiar el buen comportamiento.")],
        nos="Una empresa de {ciudad} que cuida a las mascotas",
        f_banda="photo-1516453734593-8d198ae84bcf", banda="¿Tiene una tienda de mascotas o una veterinaria?",
        banda_txt="Venda nuestros productos con precios por volumen y entregas programadas.", banda_btn="Quiero ser distribuidor")),
    (("alimento", "panader", "comida", "restaur", "bebida", "café", "cafe", "cárnic", "carnic"), dict(
        color="#A93A0B", tipo="productos", ceja="Alimentos",
        hero="Sabor y calidad, hechos en {ciudad}",
        sub="Productos frescos, preparados con cuidado y con el sabor de siempre.",
        f_hero="photo-1568254183919-78a4f43a2877", f_nos="photo-1622021142947-da7dedc7c39a",
        sec="Lo que preparamos para usted",
        servicios=[("photo-1608198093002-ad4e005484ec", "Nuestros productos", "Preparados a diario con ingredientes seleccionados."),
                   ("photo-1555244162-803834f70033", "Pedidos especiales", "Para eventos, empresas y celebraciones."),
                   ("photo-1617347454431-f49d7ff5c3b1", "Domicilios", "Lo llevamos hasta su casa u oficina.")],
        nos="Una empresa de {ciudad} con sabor propio",
        f_banda="photo-1636057915164-0cc9d6a54975", banda="¿Tiene una tienda, restaurante o minimercado?",
        banda_txt="Le ofrecemos precios por volumen y entregas programadas.", banda_btn="Quiero comprar al por mayor")),
    (("fabricaci", "elaboraci", "confecci", "producci", "manufact", "textil"), dict(
        color="#1D4ED8", tipo="productos", ceja="Fabricación",
        hero="Fabricamos en {ciudad} con calidad que se nota",
        sub="Producción propia, materiales seleccionados y entregas cumplidas para hogares y empresas.",
        f_hero="photo-1610891015188-5369212db097", f_nos="photo-1598299803204-b73796f43289",
        sec="Producción propia, a su medida",
        servicios=[("photo-1587293852726-70cdb56c2866", "Nuestros productos", "Referencias estándar listas para despacho."),
                   ("photo-1521168186168-0af572a9256e", "Pedidos a la medida", "Fabricamos según sus especificaciones."),
                   ("photo-1580674285054-bed31e145f59", "Despachos", "Entregas en la ciudad y envíos a todo el país.")],
        nos="Una fábrica de {ciudad} que cumple",
        f_banda="photo-1644079446600-219068676743", banda="¿Compra para su empresa o para distribuir?",
        banda_txt="Manejamos volúmenes, precios por cantidad y entregas programadas.", banda_btn="Pedir cotización")),
    (("construcci", "obra", "instalac", "terminaci", "arquitect", "remodel", "edific", "demolici"), dict(
        color="#A14A07", tipo="servicios", ceja="Construcción",
        hero="Construimos y remodelamos en {ciudad}",
        sub="Obras bien hechas, a tiempo y con presupuesto claro desde el primer día.",
        f_hero="photo-1541888946425-d81bb19240f5", f_nos="photo-1503387762-592deb58ef4e",
        sec="Obras residenciales y comerciales",
        servicios=[("photo-1587582423116-ec07293f0395", "Obra nueva", "Proyectos residenciales y comerciales, de los cimientos a la entrega."),
                   ("photo-1618832515490-e181c4794a45", "Remodelaciones", "Adecuaciones, acabados y mantenimiento de espacios."),
                   ("photo-1504307651254-35680f356dfd", "Instalaciones", "Redes hidrosanitarias, eléctricas y de gas.")],
        nos="Una constructora de {ciudad} que entrega a tiempo",
        f_banda="photo-1589939705384-5185137a7f0f", banda="¿Tiene un proyecto en mente?",
        banda_txt="Visitamos su obra y le entregamos el presupuesto por escrito, sin costo.", banda_btn="Pedir presupuesto")),
    (("transporte", "carga", "logíst", "logist", "mensajer", "mudanz"), dict(
        color="#0F766E", tipo="servicios", ceja="Transporte y logística",
        hero="Movemos su carga en {ciudad} y todo el país",
        sub="Transporte seguro, puntual y con seguimiento de principio a fin.",
        f_hero="photo-1766785368863-f2188a8c8b32", f_nos="photo-1627309366653-2dedc084cdf1",
        sec="Transporte a la medida de su operación",
        servicios=[("photo-1586528116311-ad8dd3c8310d", "Carga y mercancía", "Despachos para empresas y comercios."),
                   ("photo-1587813369290-091c9d432daf", "Rutas urbanas", "Entregas en el área metropolitana."),
                   ("photo-1528797249377-0257bfb1a183", "Nacional", "Envíos a las principales ciudades del país.")],
        nos="Una empresa de {ciudad} que llega a tiempo",
        f_banda="photo-1617909517054-64d4958be1c9", banda="¿Necesita mover mercancía?",
        banda_txt="Cuéntenos origen, destino y volumen, y le damos el valor el mismo día.", banda_btn="Cotizar envío")),
    (("cultivo", "cría", "cria ", "agrícol", "agricol", "ganad", "pecuar", "agropec", "palma"), dict(
        color="#15803D", tipo="productos", ceja="Agro",
        hero="Del campo de Antioquia a su mesa",
        sub="Producción agropecuaria con buenas prácticas y calidad constante.",
        f_hero="photo-1560493676-04071c5f467b", f_nos="photo-1537721664796-76f77222a5d0",
        sec="Producción del campo con buenas prácticas",
        servicios=[("photo-1524486361537-8ad15938e1a3", "Nuestra producción", "Cosechas cuidadas, con trazabilidad."),
                   ("photo-1603314947668-e8372e0075ad", "Venta a empresas", "Abastecemos industrias, mayoristas y comercios."),
                   ("photo-1615811361523-6bd03d7748e7", "Despachos", "Entregas programadas a su bodega.")],
        nos="Una empresa del campo antioqueño",
        f_banda="photo-1623136299195-570a06bdae6b", banda="¿Compra para su negocio?",
        banda_txt="Programamos entregas por volumen y temporada.", banda_btn="Pedir cotización")),
    (("médic", "medic", "odontol", "salud", "terap", "farmac", "estétic", "estetic", "diagnóstic"), dict(
        color="#0E7490", tipo="servicios", ceja="Salud",
        hero="Cuidamos su salud en {ciudad}",
        sub="Atención profesional, cercana y con todos los protocolos.",
        f_hero="photo-1631217868264-e5b90bb7e133", f_nos="photo-1516841273335-e39b37888115",
        sec="Atención cercana y profesional",
        servicios=[("photo-1666214277730-e9c7e755e5a3", "Consulta", "Atención personalizada, con tiempo para escucharle."),
                   ("photo-1504813184591-01572f98c85f", "Nuestro equipo", "Profesionales con experiencia y vocación."),
                   ("photo-1519494026892-80bbd2d6fd0d", "Citas", "Agende en pocos minutos.")],
        nos="Un equipo de salud en {ciudad}",
        f_banda="photo-1577962917302-cd874c4e31d2", banda="¿Busca un programa de salud para su empresa?",
        banda_txt="Diseñamos planes de atención para equipos de trabajo.", banda_btn="Hablar con nosotros")),
    (("comercio", "venta", "tienda", "distribu"), dict(
        color="#BE123C", tipo="productos", ceja="Comercio",
        hero="Lo que busca, al mejor precio en {ciudad}",
        sub="Variedad, buen servicio y entregas rápidas para hogares y negocios.",
        f_hero="photo-1441986300917-64674bd600d8", f_nos="photo-1739302750685-122e3b768b5a",
        sec="Variedad y buen servicio",
        servicios=[("photo-1515706886582-54c73c5eaf41", "Catálogo", "Productos seleccionados, siempre disponibles."),
                   ("photo-1768987439370-bd60d3d0b28b", "Pedidos en línea", "Pida por WhatsApp o por la página."),
                   ("photo-1614018453562-77f6180ce036", "Domicilios", "Entregas rápidas en la ciudad.")],
        nos="Un comercio de {ciudad} con atención cercana",
        f_banda="photo-1684695749267-233af13276d0", banda="¿Compra para su negocio?",
        banda_txt="Tenemos precios especiales por volumen.", banda_btn="Ver precios al por mayor")),
    ((), dict(
        color="#1F7A5A", tipo="servicios", ceja="Servicios profesionales",
        hero="Servicios profesionales que dan resultados",
        sub="Experiencia, cumplimiento y acompañamiento cercano desde {ciudad}.",
        f_hero="photo-1542744173-8e7e53415bb0", f_nos="photo-1606857521015-7f9fcf423740",
        sec="Cómo podemos ayudarle",
        servicios=[("photo-1454165804606-c3d57bc86b40", "Diagnóstico", "Entendemos su situación antes de proponer."),
                   ("photo-1517245386807-bb43f82c33c4", "Soluciones a la medida", "Un plan pensado para su empresa, no una plantilla."),
                   ("photo-1553877522-43269d4ea984", "Acompañamiento", "Le seguimos de cerca hasta ver los resultados.")],
        nos="Un equipo de {ciudad} comprometido con su empresa",
        f_banda="photo-1604881988758-f76ad2f7aac1", banda="¿Hablamos de su empresa?",
        banda_txt="La primera conversación no tiene costo.", banda_btn="Agendar una cita")),
]


# Almacén agropecuario: lo decide el NOMBRE (el RUES suele decir «comercio de
# productos farmacéuticos» o «materias primas agropecuarias»).
_AGRO_TIENDA = dict(
    color="#3F6212", tipo="productos", ceja="Agropecuaria",
    hero="Todo para el campo y sus animales",
    sub="Concentrados, salud animal e insumos agrícolas, con asesoría de gente que conoce el campo.",
    f_hero="photo-1612225330565-f79af9f92168", f_nos="photo-1650397306390-86caaefce1a3",
    sec="Lo que necesita su finca",
    servicios=[("photo-1740126104974-e5bad141f188", "Concentrados y alimentos", "Para ganado, caballos, aves y mascotas."),
               ("photo-1685633224332-3b270212cf63", "Salud animal", "Medicamentos veterinarios, vitaminas y vacunas."),
               ("photo-1625246333195-78d9c38ad449", "Insumos agrícolas", "Semillas, abonos y productos para sus cultivos.")],
    nos="Una agropecuaria al servicio del campo antioqueño",
    f_banda="photo-1504867991871-57c29096e624", banda="¿Compra para su finca o su negocio?",
    banda_txt="Le damos precios por volumen y le despachamos el pedido.", banda_btn="Pedir cotización")


# Deportes y recreación (escuelas deportivas, gimnasios, clubes). Va antes del
# perfil general, que es el último de la lista.
_PERFILES.insert(len(_PERFILES) - 1, (("deport", "recreativ", "gimnas", "fitness", "acondicionamiento físico", "atracciones", "esparcimiento"), dict(
    color="#B45309", tipo="servicios", ceja="Deporte y recreación",
    hero="Deporte y diversión para toda la familia en {ciudad}",
    sub="Clases, entrenamiento y actividades recreativas para niños, jóvenes y adultos.",
    f_hero="photo-1526232761682-d26e03ac148e", f_nos="photo-1609422644211-a85c36ee36a7",
    sec="Actividades para cada edad",
    servicios=[("photo-1544333323-ec9ed3218dd1", "Escuela deportiva", "Formación por niveles con entrenadores de experiencia."),
               ("photo-1787647089849-d082bb425a4a", "Clases grupales", "Grupos pequeños y horarios entre semana y fines de semana."),
               ("photo-1574629810360-7efbbe195018", "Entrenamiento", "Planes para mejorar técnica, condición física y disciplina.")],
    nos="Un espacio en {ciudad} para aprender jugando",
    f_banda="photo-1787647089627-5866477a33f1", banda="¿Quiere inscribirse?",
    banda_txt="Escríbanos y le contamos horarios, grupos y valores.", banda_btn="Pedir información")))

# Escuela de equitación / haras: lo decide el NOMBRE (el RUES dice solo
# «enseñanza deportiva y recreativa» o «cría de caballos»).
_ECUESTRE = dict(
    color="#7C2D12", tipo="servicios", ceja="Escuela ecuestre",
    hero="Equitación con pasión y respeto por el caballo",
    sub="Clases para niños y adultos, salto y pesebrera en {ciudad}.",
    f_hero="photo-1726209431921-71cb661b4dbf", f_nos="photo-1590839823121-c253bf8d1e7d",
    sec="Viva la experiencia del caballo",
    servicios=[("photo-1607273225241-035d579b8452", "Clases de equitación", "Desde el primer contacto con el caballo hasta montar con seguridad."),
               ("photo-1789985478605-9485876472e4", "Salto y entrenamiento", "Preparación para jinetes que quieren competir."),
               ("photo-1576692192914-9abed71b3ef9", "Pesebrera", "Alojamiento, alimentación y cuidado diario de su caballo.")],
    nos="Un haras en {ciudad} para aprender y disfrutar",
    f_banda="photo-1511746687876-42cb762f6ac1", banda="¿Quiere su primera clase?",
    banda_txt="Agende una clase de prueba y conozca nuestras instalaciones.", banda_btn="Agendar clase")


def _perf(color, tipo, ceja, hero, sub, fotos, sec, servicios, nos, banda, banda_txt, banda_btn):
    """Arma un perfil con las 6 fotos en orden: portada, nosotros, 3 servicios y banda."""
    fh, fn, s1, s2, s3, fb = ("photo-" + f for f in fotos)
    return dict(color=color, tipo=tipo, ceja=ceja, hero=hero, sub=sub, f_hero=fh, f_nos=fn, sec=sec,
                servicios=[(s1,) + servicios[0], (s2,) + servicios[1], (s3,) + servicios[2]],
                nos=nos, f_banda=fb, banda=banda, banda_txt=banda_txt, banda_btn=banda_btn)


# Sectores que caían en el perfil general o en uno equivocado (revisión del
# 11-oct sobre todo el RUES de la prospección). Se buscan en la ACTIVIDAD antes
# que los demás perfiles, porque sus claves son más específicas: «producción de
# películas» no es fábrica, «colocación de empleo» no es turismo.
_ESPECIALES = [
    (("vehícul", "vehicul", "motocic", "autopart", "automotor"), _perf(
        "#B91C1C", "servicios", "Automotriz", "Su vehículo en buenas manos en {ciudad}",
        "Repuestos, mantenimiento y asesoría para carros y motos.",
        ("1771340012319-0b4fca008b54", "1676018366904-c083ed678e60", "1487754180451-c456f719a1fc",
         "1527383418406-f85a3b146499", "1585390062628-be8608aa7d83", "1621808752171-531c30903889"),
        "Todo para su vehículo",
        [("Taller y mantenimiento", "Revisión, cambio de aceite y reparaciones con garantía."),
         ("Repuestos y accesorios", "Partes originales y homologadas para su marca."),
         ("Asesoría en compra", "Le ayudamos a escoger el vehículo o la pieza correcta.")],
        "Gente de {ciudad} que sabe de vehículos", "¿Necesita un repuesto o una revisión?",
        "Escríbanos la marca y el modelo y le damos precio y disponibilidad.", "Pedir cotización")),
    (("películ", "cinematogr", "televisi", "grabaci", "fotograf", "publicidad", "audiovis"), _perf(
        "#9D174D", "servicios", "Publicidad y audiovisual", "Contenido que hace que su marca se vea",
        "Video, fotografía y publicidad para empresas que quieren crecer.",
        ("1497015289639-54688650d173", "1603201667230-bd139210db18", "1459184070881-58235578f004",
         "1647427854253-b92bb40c9330", "1572021335469-31706a17aaef", "1471341971476-ae15ff5dd4ea"),
        "Lo que hacemos por su marca",
        [("Producción de video", "Comerciales, videos corporativos y contenido para redes."),
         ("Fotografía", "Producto, eventos y retratos con estudio propio."),
         ("Publicidad y estrategia", "Campañas pensadas para vender, no solo para verse bien.")],
        "Un equipo creativo en {ciudad}", "¿Tiene un proyecto en mente?",
        "Cuéntenos la idea y le enviamos propuesta y presupuesto.", "Contar mi proyecto")),
    (("jurídic", "juridic", "abogad", "notari"), _perf(
        "#1E3A5F", "servicios", "Abogados", "Asesoría legal clara y oportuna en {ciudad}",
        "Acompañamos a personas y empresas en cada decisión jurídica.",
        ("1505664194779-8beaceb93744", "1758518731462-d091b0b4ed0d", "1774898988393-5c752e4d55e9",
         "1450101499163-c8848c66ca85", "1603796846097-bee99e4a601f", "1479142506502-19b3a3b7ff33"),
        "Áreas de práctica",
        [("Asesoría empresarial", "Sociedades, contratos y cumplimiento para su empresa."),
         ("Contratos", "Redacción y revisión para que firme con tranquilidad."),
         ("Litigios y trámites", "Le representamos y hacemos seguimiento a cada proceso.")],
        "Abogados en {ciudad} de su lado", "¿Tiene una consulta legal?",
        "Agende una primera conversación y le decimos cómo podemos ayudarle.", "Agendar consulta")),
    (("contabilidad", "teneduría", "tenedur", "asesoría tributaria", "revisoría"), _perf(
        "#065F46", "servicios", "Contabilidad y finanzas", "Sus números claros y al día",
        "Contabilidad, impuestos y asesoría financiera para empresas en {ciudad}.",
        ("1648201637025-1c77b9be3013", "1526948531399-320e7e40f0ca", "1711097383282-28097ae16b1d",
         "1626266061368-46a8f578ddd6", "1714974528737-3e6c7e4d11af", "1618044733300-9472054094ee"),
        "Cómo le ayudamos",
        [("Contabilidad", "Registros, estados financieros e informes cada mes."),
         ("Impuestos", "Declaraciones a tiempo y planeación tributaria."),
         ("Asesoría financiera", "Números que sirven para decidir.")],
        "Un equipo contable en {ciudad}", "¿Quiere tener su contabilidad al día?",
        "Escríbanos y le contamos cómo trabajamos.", "Hablar con un asesor")),
    (("actividades de ingenier", "consultoría técnica", "consultoria tecnica"), _perf(
        "#1D4ED8", "servicios", "Ingeniería", "Ingeniería que convierte ideas en proyectos",
        "Diseño, consultoría técnica e interventoría en {ciudad}.",
        ("1581094488379-6a10d04c0f04", "1694521787193-9293daeddbaa", "1542621334-a254cf47733d",
         "1694521787162-5373b598945c", "1621905252507-b35492cc74b4", "1503387762-592deb58ef4e"),
        "Nuestros servicios",
        [("Diseño y planos", "Diseños técnicos y memorias de cálculo."),
         ("Interventoría y obra", "Seguimiento en campo para que se cumpla lo diseñado."),
         ("Consultoría técnica", "Estudios y conceptos para tomar buenas decisiones.")],
        "Ingenieros de {ciudad} comprometidos con cada proyecto", "¿Tiene un proyecto por diseñar?",
        "Cuéntenos el alcance y le enviamos la propuesta técnica.", "Pedir propuesta")),
    (("extracci", "minería", "mineria", "minero"), _perf(
        "#92400E", "servicios", "Minería", "Minería responsable en Antioquia",
        "Exploración, extracción y operación minera con seguridad y respeto por el entorno.",
        ("1587919968590-fbc98cea6c9a", "1691068343106-159ec5b344e9", "1719411321415-acfbe793c0aa",
         "1610375461246-83df859d849d", "1586161148512-64a1b3dac527", "1571223641822-b82408a0e705"),
        "Lo que hacemos",
        [("Extracción", "Operación con maquinaria y personal capacitado."),
         ("Metales preciosos", "Beneficio y comercialización con trazabilidad."),
         ("Seguridad minera", "Protocolos para cuidar a nuestra gente.")],
        "Una empresa minera de {ciudad}", "¿Busca un aliado minero?",
        "Escríbanos y conversemos sobre su proyecto.", "Contactar")),
    (("call center", "centros de llamadas", "contact center"), _perf(
        "#0F766E", "servicios", "Contact center", "Atención a sus clientes, bien hecha",
        "Servicio al cliente, ventas y soporte por teléfono, chat y correo.",
        ("1560264280-88b68371db39", "1766066014237-00645c74e9c6", "1603714228681-b399854b8f80",
         "1553775282-20af80779df7", "1580795479225-c50ab8c3348d", "1580795478966-561ba4f1ce68"),
        "Servicios para su empresa",
        [("Servicio al cliente", "Agentes entrenados que hablan como su marca."),
         ("Ventas y cobranza", "Campañas de salida con metas claras."),
         ("Soporte y chat", "Atención por todos los canales con reportes diarios.")],
        "Un equipo de atención en {ciudad}", "¿Quiere atender mejor a sus clientes?",
        "Le armamos una propuesta según su volumen de llamadas.", "Pedir propuesta")),
    (("talento humano", "empleo", "temporal", "selección de personal"), _perf(
        "#7C3AED", "servicios", "Talento humano", "El personal correcto para su empresa",
        "Selección, contratación y suministro de personal en {ciudad}.",
        ("1698047682091-782b1e5c6536", "1576267423429-569309b31e84", "1573497620053-ea5300f94f21",
         "1548838670-cb67b43a6adb", "1758518731468-98e90ffd7430", "1521791136064-7986c2920216"),
        "Cómo le ayudamos",
        [("Selección", "Encontramos y evaluamos a los candidatos por usted."),
         ("Personal operativo", "Equipos para obra, planta y logística."),
         ("Personal administrativo", "Perfiles de oficina listos para empezar.")],
        "Un equipo de {ciudad} que conecta empresas y personas", "¿Necesita personal?",
        "Cuéntenos el cargo y le enviamos candidatos.", "Solicitar personal")),
    (("alquiler", "arrendamiento de"), _perf(
        "#CA8A04", "servicios", "Alquiler de equipos", "Maquinaria y equipos cuando los necesita",
        "Alquiler de maquinaria y equipos para obra e industria en {ciudad}.",
        ("1652303713917-2666b8bee507", "1575281923032-f40d94ef6160", "1580901369227-308f6f40bdeb",
         "1591486085897-f433f05e7aed", "1685708716815-94c588417c27", "1495036019936-220b29b930ea"),
        "Equipos disponibles",
        [("Maquinaria amarilla", "Excavadoras, retroexcavadoras y más, con o sin operador."),
         ("Compactación", "Equipos para vías, patios y obras."),
         ("Transporte de equipos", "Llevamos la máquina hasta su obra.")],
        "Una empresa de {ciudad} que mueve sus proyectos", "¿Qué equipo necesita?",
        "Díganos la obra y las fechas y le damos el valor por día o por mes.", "Cotizar alquiler")),
    (("peluquer", "belleza", "barber", "servicios personales", "manicur"), _perf(
        "#BE185D", "servicios", "Belleza y cuidado personal", "Su mejor versión en {ciudad}",
        "Corte, color, barbería y tratamientos con profesionales.",
        ("1633681926022-84c23e8cb2d6", "1580618672591-eb180b1a973f", "1634449571010-02389ed0f9b0",
         "1585747860715-2ba37e788b70", "1621605815971-fbc98d665033", "1600948836101-f9ffda59d250"),
        "Nuestros servicios",
        [("Peluquería", "Corte, color y peinados para cada ocasión."),
         ("Barbería", "Corte y arreglo de barba con estilo."),
         ("Tratamientos", "Cuidado del cabello y de la piel.")],
        "Un espacio en {ciudad} para consentirse", "¿Agendamos su cita?",
        "Escríbanos y le asignamos el horario que mejor le quede.", "Agendar cita")),
    (("mantenimiento y reparaci",), _perf(
        "#0369A1", "servicios", "Mantenimiento y reparación", "Equipos funcionando, operación sin pausas",
        "Mantenimiento preventivo y correctivo de maquinaria y equipos en {ciudad}.",
        ("1595831708961-1b13c0dd2422", "1610259998914-d1b9afe0dc55", "1563456021008-5cd6ac7c005d",
         "1660330589827-da8ab7dd3c02", "1563770660941-20978e870e26", "1620374142148-15bb477652c1"),
        "Lo que reparamos",
        [("Maquinaria industrial", "Diagnóstico y reparación en planta."),
         ("Sistemas eléctricos", "Tableros, motores y controles."),
         ("Equipos electrónicos", "Reparación de tarjetas y equipos especializados.")],
        "Técnicos de {ciudad} con experiencia", "¿Un equipo se le dañó?",
        "Escríbanos y le programamos la visita técnica.", "Pedir visita técnica")),
    (("reciclaj", "recuperación de materiales", "recuperacion de materiales", "desperdicio", "chatarra"), _perf(
        "#15803D", "servicios", "Reciclaje", "Lo que sobra también vale",
        "Compra, recolección y aprovechamiento de materiales reciclables en {ciudad}.",
        ("1617303331806-3d6b58e03241", "1536094627107-abf98dedaa8f", "1653406384710-08688ec6b979",
         "1600368544663-05493ab49549", "1611284446314-60a58ac0deb9", "1722695510527-cc033e43be1b"),
        "Materiales que recibimos",
        [("Plástico y cartón", "Recolección en empresas y conjuntos."),
         ("Chatarra y metales", "Compra al mejor precio, pesaje justo."),
         ("Gestión de residuos", "Separación en la fuente y certificados de aprovechamiento.")],
        "Una empresa de {ciudad} que cuida el ambiente", "¿Tiene material para reciclar?",
        "Escríbanos y programamos la recolección.", "Programar recolección")),
    (("educaci", "preescolar", "jardín infantil", "jardin infantil"), _perf(
        "#EA580C", "servicios", "Educación", "Aprender jugando, crecer felices",
        "Educación con amor y acompañamiento en {ciudad}.",
        ("1567746455504-cb3213f8f5b8", "1583468991267-3f068b607ae1", "1614113036347-9f60df80730a",
         "1589206946274-929e4da3996b", "1578349035260-9f3d4042f1f7", "1627764940620-90393d0e8c34"),
        "Nuestra propuesta",
        [("Juego y exploración", "Aprendizaje a través del juego y la curiosidad."),
         ("Acompañamiento", "Docentes cercanos y grupos pequeños."),
         ("Espacios seguros", "Lugares pensados para que los niños se desarrollen.")],
        "Un lugar en {ciudad} para aprender", "¿Quiere conocernos?",
        "Agende una visita y conozca nuestras instalaciones.", "Agendar visita")),
    (("energía", "energia", "solar", "fotovolta"), _perf(
        "#CA8A04", "servicios", "Energía", "Energía confiable para su hogar o empresa",
        "Soluciones de energía y paneles solares en {ciudad}.",
        ("1509391366360-2e959784a276", "1624397640148-949b1732bb0a", "1497435334941-8c899ee9e8e9",
         "1413882353314-73389f63b6fd", "1660330589257-813305a4a383", "1613665813446-82a78c468a1d"),
        "Nuestras soluciones",
        [("Energía solar", "Diseño e instalación de sistemas fotovoltaicos."),
         ("Suministro de energía", "Energía para empresas con tarifas competitivas."),
         ("Instalación y mantenimiento", "Técnicos certificados en cada proyecto.")],
        "Una empresa de energía en {ciudad}", "¿Quiere ahorrar en energía?",
        "Envíenos su factura y le calculamos el ahorro.", "Calcular mi ahorro")),
]


def _hay(clave: str, texto: str) -> bool:
    """La clave al comienzo de una palabra («obra» no está en «cobranza»)."""
    return re.search(r"\b" + re.escape(clave.strip()), texto) is not None


def perfil(actividad: str, nombre: str = "") -> dict:
    a = (actividad or "").lower().strip()
    n = (nombre or "").lower()
    if any(_hay(k, n) for k in ("haras", "ecuestre", "hípic", "hipic", "equitaci", "caballeriz")):
        return _ECUESTRE
    if "agropecuari" in n or "agroveterin" in n or "agroinsum" in n:
        return _AGRO_TIENDA
    if "veterinari" in n:
        return next(p for c, p in _PERFILES if "veterin" in c)
    for claves, p in _ESPECIALES:
        if any(_hay(k, a) for k in claves):
            return p
    if a.startswith("comercio") and "vehícul" not in a and "motocic" not in a and "partes" not in a:
        return next(p for c, p in _PERFILES if "comercio" in c)
    for claves, p in _PERFILES:
        if claves and any(_hay(k, a) for k in claves):
            return p
    # La actividad no dice nada útil («ingeniería», «administración»…): probar con el nombre.
    for claves, p in _PERFILES:
        if claves and any(_hay(k, n) for k in claves):
            return p
    return _PERFILES[-1][1]


def _tinte(hexa: str, k: float = 0.92) -> str:
    """El color de la marca, casi blanco (fondos suaves)."""
    r, g, b = (int(hexa[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(round(c + (255 - c) * k) for c in (r, g, b))


# «Por qué elegirnos» y preguntas frecuentes según vendan productos o servicios.
# Solo afirmaciones genéricas que cualquier empresa formal puede sostener: nada de
# cifras, años de experiencia, premios ni precios. {nit_txt} = «con NIT 9001…» o «».
_POR_QUE = {
    "servicios": [("chat", "Atención directa", "Habla con quien hace el trabajo, sin intermediarios ni call center."),
                  ("doc", "Propuesta por escrito", "Alcance, tiempos y valor claros antes de empezar."),
                  ("check", "Compromiso con lo acordado", "Cumplimos lo pactado y le mantenemos informado en cada paso."),
                  ("pin", "Empresa de {ciudad}", "Cerca de usted y legalmente constituida{nit_txt}.")],
    "productos": [("chat", "Asesoría al comprar", "Le ayudamos a elegir lo que de verdad necesita."),
                  ("doc", "Cotización clara", "Referencias, presentaciones y valores por escrito."),
                  ("truck", "Entregas en {ciudad}", "Le llevamos su pedido en {ciudad} y el área metropolitana."),
                  ("shield", "Compra con respaldo", "Empresa legalmente constituida{nit_txt}, con factura en cada compra.")],
}
_FAQ = {
    "servicios": [("¿Cómo solicito una cotización?",
                   "Escríbanos por el formulario de esta página o a nuestro correo contándonos qué necesita. "
                   "Le respondemos con una propuesta por escrito."),
                  ("¿La primera conversación tiene costo?",
                   "No. La primera conversación es para entender su necesidad y no tiene costo ni compromiso."),
                  ("¿Atienden fuera de {ciudad}?",
                   "Nuestra base está en {ciudad}. Si usted está en otro municipio, cuéntenos dónde y le confirmamos si podemos atenderle."),
                  ("¿Entregan factura?",
                   "Sí. Somos una empresa legalmente constituida{nit_txt} y cada servicio queda respaldado con su factura.")],
    "productos": [("¿Cómo hago un pedido?",
                   "Escríbanos por el formulario de esta página o a nuestro correo con los productos y cantidades que necesita. "
                   "Le enviamos la cotización y coordinamos la entrega."),
                  ("¿Hacen entregas?",
                   "Sí, entregamos en {ciudad} y el área metropolitana. Para otros municipios, consúltenos."),
                  ("¿Atienden pedidos para negocios?",
                   "Sí. Cuéntenos las cantidades que maneja su negocio y le enviamos la cotización correspondiente."),
                  ("¿Qué medios de pago reciben?",
                   "Se los confirmamos junto con la cotización, según el pedido y la forma de entrega.")],
}
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")
# Correos gratuitos: con ellos no hay dominio propio de dónde sacar el logo.
_CORREO_GRATIS = ("gmail.", "googlemail.", "hotmail.", "outlook.", "live.", "msn.", "yahoo.", "ymail.",
                  "icloud.", "me.com", "mac.com", "aol.", "proton", "pm.me", "zoho.", "gmx.", "mail.com", "une.net")


def dominio_propio(email: str) -> str:
    """«chris@cardanel.com» → «cardanel.com»; «x@gmail.com» → «» (correo gratuito)."""
    d = (email or "").rsplit("@", 1)[-1].lower().strip() if "@" in (email or "") else ""
    return "" if not d or any(d.startswith(g) or d == g for g in _CORREO_GRATIS) else d


def _fecha_larga(iso: str) -> tuple[str, str]:
    """«2026-09-10» → («2026», «10 de septiembre de 2026»); vacío si no sirve."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", (iso or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12:
        return "", ""
    a, mes, d = m.groups()
    return a, f"{int(d)} de {_MESES[int(mes) - 1]} de {a}"


def datos_sitio(p) -> dict:
    """Todo lo que la plantilla necesita a partir del Prospecto."""
    c = ciudad(p.municipio)
    pf = perfil(p.actividad, p.razon_social)
    f = lambda t: t.format(ciudad=c)
    nombre = (p.razon_social or "").strip()
    act = (p.actividad or "").strip().rstrip(".")
    wa_txt = quote(f"Hola Edison, quiero publicar la página web de {nombre}")
    anio, matricula = _fecha_larga(getattr(p, "fecha_matricula", "") or "")
    nit_txt = f" (NIT {p.nit})" if p.nit else ""
    f2 = lambda t: t.format(ciudad=c, nit_txt=nit_txt)
    return {"razon_social": nombre, "marca": marca(nombre), "inicial": (marca(nombre) or "E")[0].upper(),
            "ciudad": c, "correo": p.email, "nit": p.nit or "", "actividad": act,
            "color": pf["color"], "tinte": _tinte(pf["color"]), "claro": _tinte(pf["color"], 0.55), "tipo": pf["tipo"],
            "ceja": f"{pf['ceja']} · {c}", "hero": f(pf["hero"]), "sub": f(pf["sub"]),
            "f_hero": _u(pf["f_hero"], 1800), "f_nos": _u(pf["f_nos"], 1100), "f_banda": _u(pf["f_banda"], 1000),
            "sec": pf["sec"], "servicios": [(_u(ft, 800), t, d) for ft, t, d in pf["servicios"]],
            "nos": f(pf["nos"]),
            "nos_txt": f"Somos {nombre}, dedicados a {act[:1].lower() + act[1:] if act else 'servir a nuestros clientes'}. "
                       f"Trabajamos para que nuestros clientes en {c} tengan una empresa cercana, seria y con atención directa.",
            "pasos": [(t, f(d)) for t, d in _PASOS[pf["tipo"]]],
            "banda": pf["banda"], "banda_txt": pf["banda_txt"], "banda_btn": pf["banda_btn"],
            "precio_diseno": f"${PRECIO_DISENO:,.0f}".replace(",", "."),
            "precio_anual": f"${PRECIO_ANUAL:,.0f}".replace(",", "."),
            "wa_publicar": f"https://wa.me/573332470715?text={wa_txt}",
            # Rediseño 11-oct: datos REALES del registro y textos genéricos por tipo.
            "anio": anio, "matricula": matricula,
            "por_que": [(i, f2(t), f2(d)) for i, t, d in pf.get("por_que") or _POR_QUE[pf["tipo"]]],
            "faq": [(f2(q), f2(r)) for q, r in pf.get("faq") or _FAQ[pf["tipo"]]],
            "galeria": [_u(pf["f_hero"], 900), _u(pf["servicios"][0][0], 700), _u(pf["f_nos"], 700),
                        _u(pf["servicios"][1][0], 700), _u(pf["f_banda"], 900), _u(pf["servicios"][2][0], 700)],
            "mapa": "https://www.google.com/maps?q=" + quote(f"{c}, Antioquia, Colombia") + "&output=embed",
            "dominio": dominio_propio(p.email),
            "logo_url": (getattr(p, "logo_url", "") or "").strip()}
