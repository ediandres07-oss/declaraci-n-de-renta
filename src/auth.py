"""Autenticación social (Google / Microsoft) y base de datos de usuarios.

Guarda el correo del usuario para enviarle recordatorios de su declaración,
y su cédula/NIT para calcular la fecha de vencimiento. Todo local en SQLite.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from authlib.integrations.flask_client import OAuth
from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
oauth = OAuth()

BASE = Path(__file__).resolve().parent.parent
_ACCESO_PATH = BASE / "config" / "acceso.yaml"

# Rutas candidatas para la configuración OAuth, en orden de preferencia.
# En local vive en config/oauth.yaml. En Render se carga como Secret File: su
# panel no admite '/' en el nombre, así que el archivo se llama 'oauth.yaml' y
# Render lo monta en /etc/secrets/oauth.yaml (y también en la raíz del proyecto).
_OAUTH_PATHS = [
    BASE / "config" / "oauth.yaml",
    Path("/etc/secrets/oauth.yaml"),
    BASE / "oauth.yaml",
]


def cargar_config_oauth() -> dict:
    for ruta in _OAUTH_PATHS:
        if ruta.exists():
            with open(ruta, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
    return {}


def _correos_autorizados() -> set:
    """Lista de correos de personal autorizado (liquidador y /admin)."""
    if _ACCESO_PATH.exists():
        with open(_ACCESO_PATH, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return {str(c).strip().lower() for c in (cfg.get("autorizados") or [])}
    return set()


def es_autorizado(usuario) -> bool:
    """Personal autorizado: correo en la lista Y autenticado por un proveedor
    real (Google/Microsoft). Los ingresos 'demo' nunca cuentan como admin."""
    if not usuario or usuario.proveedor not in ("google", "microsoft"):
        return False
    return (usuario.email or "").lower() in _correos_autorizados()


# ---------------------------------------------------------------- modelo
class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    proveedor = db.Column(db.String(20))          # google | microsoft
    proveedor_id = db.Column(db.String(120))      # id único del proveedor
    email = db.Column(db.String(200), index=True)
    nombre = db.Column(db.String(200))
    foto = db.Column(db.String(400))
    cedula = db.Column(db.String(30))             # cédula/NIT que ingresa el usuario
    fecha_limite = db.Column(db.Date)             # vencimiento calculado
    acepta_recordatorios = db.Column(db.Boolean, default=True)
    quiere_asesor = db.Column(db.Boolean, default=False)
    # año en que ya se envió cada recordatorio (evita duplicados; se reinicia por año)
    recordatorio_30_year = db.Column(db.Integer)
    recordatorio_7_year = db.Column(db.Integer)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_acceso = db.Column(db.DateTime, default=datetime.utcnow)
    # segundo factor (TOTP)
    mfa_habilitado = db.Column(db.Boolean, default=False)
    intentos_fallidos = db.Column(db.Integer, default=0)
    bloqueado_hasta = db.Column(db.DateTime)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "nombre": self.nombre,
            "foto": self.foto,
            "cedula": self.cedula,
            "fecha_limite": self.fecha_limite.isoformat() if self.fecha_limite else None,
            "acepta_recordatorios": self.acepta_recordatorios,
            "quiere_asesor": self.quiere_asesor,
            "mfa_habilitado": bool(self.mfa_habilitado),
        }


class UsuarioMFA(db.Model):
    """Segundo factor de un usuario. El secreto TOTP y los códigos de respaldo
    son material sensible: los códigos se guardan hasheados, nunca en claro."""
    __tablename__ = "usuario_mfa"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), unique=True)
    totp_secreto = db.Column(db.String(64))
    totp_habilitado = db.Column(db.Boolean, default=False)
    backup_hashes = db.Column(db.Text)            # JSON: lista de sha256 hex
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    ultimo_uso = db.Column(db.DateTime)

    usuario = db.relationship("Usuario", backref=db.backref("mfa", uselist=False))


class LeadEspera(db.Model):
    """Correo captado por la guía-obsequio: lista de espera para avisar a la
    persona cuando la DIAN habilite la exógena. Va en Postgres (no en el
    filesystem) para no perder los leads en cada despliegue de Render."""
    __tablename__ = "lista_espera"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, index=True)
    nombre = db.Column(db.String(120))
    ip = db.Column(db.String(60))
    creado = db.Column(db.DateTime, default=datetime.utcnow)


class ArchivoExogena(db.Model):
    """Excel de exógena subido por el cliente, guardado en la BD (Postgres en
    producción) para que un redeploy no borre el insumo del trámite: es el
    archivo con el que se elabora y presenta la declaración de quien ya pagó."""
    __tablename__ = "archivos_exogena"
    id = db.Column(db.String(100), primary_key=True)   # token de carga u "orden:<id>"
    nombre = db.Column(db.String(200))
    datos = db.Column(db.LargeBinary, nullable=False)
    creado = db.Column(db.DateTime, default=datetime.utcnow)


class OrdenRegistro(db.Model):
    """Órdenes y cargas de exógena, persistidas en la BD (Postgres en producción)
    para no perderlas en cada redeploy de Render. Se guarda el dict completo como
    JSON bajo su id — misma forma que tenía sessions/ordenes.json, así el flujo de
    checkout/pagos/admin no cambia de lógica, solo de almacenamiento."""
    __tablename__ = "ordenes_kv"
    id = db.Column(db.String(80), primary_key=True)   # token de carga u orden (ej. "o-ABC123")
    data = db.Column(db.Text, nullable=False)          # JSON del registro completo
    actualizado = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MuestraContador(db.Model):
    """Registra que un contador (usuario) ya usó su declaración de MUESTRA gratis.
    La sola existencia de la fila = prueba consumida. Una por usuario. Vive en la
    BD para que el límite sobreviva a los redeploys de Render."""
    __tablename__ = "muestras_contador"
    usuario_id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200))
    token = db.Column(db.String(80))                  # token de la carga usada
    nit_muestra = db.Column(db.String(30))            # cédula del cliente de la muestra
    creado = db.Column(db.DateTime, default=datetime.utcnow)


class AccesoAutorizado(db.Model):
    """Contadores habilitados al liquidador profesional desde /admin (pase de
    temporada). Vive en la BD para poder otorgar/revocar con un botón, sin editar
    el Secret File acceso.yaml ni redesplegar. NO da acceso al panel /admin: eso
    sigue restringido a la lista bootstrap de acceso.yaml (es_autorizado)."""
    __tablename__ = "accesos_autorizados"
    email = db.Column(db.String(200), primary_key=True)   # en minúsculas
    nombre = db.Column(db.String(200))
    nota = db.Column(db.String(200))                       # ej. "Pase temporada 2026"
    creado = db.Column(db.DateTime, default=datetime.utcnow)


# --------------------------------------------------------------- segundo factor
MAX_INTENTOS_MFA = 5
BLOQUEO_MINUTOS = 15
NUM_BACKUP_CODES = 10


def _hash_backup(codigo: str) -> str:
    return hashlib.sha256(codigo.strip().upper().encode()).hexdigest()


def generar_backup_codes(n: int = NUM_BACKUP_CODES) -> list:
    """Códigos de emergencia en claro. Se muestran una sola vez al usuario."""
    return [secrets.token_hex(4).upper() for _ in range(n)]


def esta_bloqueado(usuario: Usuario) -> bool:
    return bool(usuario.bloqueado_hasta and datetime.utcnow() < usuario.bloqueado_hasta)


def registrar_intento_fallido(usuario: Usuario) -> None:
    usuario.intentos_fallidos = (usuario.intentos_fallidos or 0) + 1
    if usuario.intentos_fallidos >= MAX_INTENTOS_MFA:
        usuario.bloqueado_hasta = datetime.utcnow() + timedelta(minutes=BLOQUEO_MINUTOS)
    db.session.commit()


def limpiar_intentos_fallidos(usuario: Usuario) -> None:
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    db.session.commit()


def verificar_totp(usuario: Usuario, codigo: str) -> bool:
    """Valida un código TOTP o, en su defecto, uno de respaldo (que se consume)."""
    import pyotp

    mfa = usuario.mfa
    if mfa is None or not mfa.totp_habilitado:
        return False
    codigo = (codigo or "").strip().replace(" ", "")

    if pyotp.TOTP(mfa.totp_secreto).verify(codigo, valid_window=1):
        mfa.ultimo_uso = datetime.utcnow()
        db.session.commit()
        return True

    # código de respaldo: de un solo uso
    hashes = json.loads(mfa.backup_hashes or "[]")
    objetivo = _hash_backup(codigo)
    for h in hashes:
        if hmac.compare_digest(h, objetivo):
            hashes.remove(h)
            mfa.backup_hashes = json.dumps(hashes)
            mfa.ultimo_uso = datetime.utcnow()
            db.session.commit()
            return True
    return False


# ------------------------------------------------------------- helpers sesión
def usuario_actual() -> Optional[Usuario]:
    uid = session.get("uid")
    if uid is None:
        return None
    return db.session.get(Usuario, uid)


def usuario_pendiente_mfa() -> Optional[Usuario]:
    """Usuario que ya pasó OAuth pero aún no ha superado el segundo factor.

    Vive en `uid_pendiente`, nunca en `uid`: mientras esté ahí no tiene sesión
    y los decoradores de autorización lo tratan como anónimo.
    """
    uid = session.get("uid_pendiente")
    if uid is None:
        return None
    return db.session.get(Usuario, uid)


def login_requerido(f):
    """Cualquier usuario con sesión iniciada (Google/Microsoft/demo). A diferencia
    de autorizado_requerido, no exige estar en la lista de personal autorizado —
    sirve para gatear interacciones normales de clientes (subir exógena, chat,
    checkout), no solo el acceso profesional."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if usuario_actual() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Debes iniciar sesión para continuar.",
                                "login_requerido": True}), 401
            return redirect(url_for("login_page", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def autorizado_requerido(f):
    """Solo personal autorizado (config/acceso.yaml). Requiere login primero."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        u = usuario_actual()
        if u is None:
            return redirect(url_for("login_page", next=request.path))
        if not es_autorizado(u):
            # las rutas de API responden JSON; las de página, HTML
            if request.path.startswith("/api/"):
                return jsonify({"error": "Acceso restringido a personal autorizado."}), 403
            return render_template("no_autorizado.html", email=u.email), 403
        return f(*args, **kwargs)
    return wrapper


def _correos_pase() -> set:
    """Correos de contadores con pase, habilitados desde /admin (BD)."""
    try:
        return {(a.email or "").lower() for a in AccesoAutorizado.query.all()}
    except Exception:
        # la tabla puede no existir todavía en el primer arranque
        return set()


def es_pro(usuario) -> bool:
    """Acceso al liquidador profesional: personal autorizado (acceso.yaml) o
    contador con pase habilitado desde /admin (BD). Siempre exige proveedor real.
    NO habilita el panel /admin (ese usa es_autorizado, más estricto)."""
    if not usuario or usuario.proveedor not in ("google", "microsoft"):
        return False
    if es_autorizado(usuario):
        return True
    return (usuario.email or "").lower() in _correos_pase()


def pro_requerido(f):
    """Acceso al liquidador: personal autorizado o contador con pase (es_pro)."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        u = usuario_actual()
        if u is None:
            return redirect(url_for("login_page", next=request.path))
        if not es_pro(u):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Necesitas el pase de temporada para usar "
                                "el liquidador profesional."}), 403
            return render_template("no_autorizado.html", email=u.email), 403
        return f(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------- base de datos
def uri_base_datos() -> str:
    """URI de la BD: Postgres si hay DATABASE_URL, SQLite local en caso contrario.

    En Render el disco es efímero: un SQLite en sessions/ se pierde en cada
    despliegue, y con él las cuentas y los secretos TOTP. Por eso producción
    debe apuntar a un Postgres gestionado mediante DATABASE_URL.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render entrega 'postgres://', un esquema que SQLAlchemy 2 ya no acepta.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    # La carpeta sessions/ no viene en el repo (está en .gitignore); la creamos
    # al arrancar para que SQLite pueda escribir la base de datos.
    (BASE / "sessions").mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{BASE / 'sessions' / 'usuarios.db'}"


def _clave_de_sesion(cfg: dict) -> str:
    """Clave para firmar las cookies de sesión.

    Prioriza la variable de entorno para que en producción no dependa de un
    archivo de configuración. La constante de desarrollo es pública (está en el
    repositorio): con ella, cualquiera podría falsificar una cookie de sesión y
    hacerse pasar por un usuario autorizado, así que nunca debe usarse fuera de
    la máquina local.
    """
    clave = os.environ.get("SECRET_KEY") or cfg.get("secret_key")
    if clave:
        return clave
    if os.environ.get("RENDER") or os.environ.get("DATABASE_URL"):
        raise RuntimeError(
            "Falta SECRET_KEY en producción. Defínala como variable de entorno "
            "o incluya 'secret_key' en config/oauth.yaml (Secret File)."
        )
    return "clave-temporal-de-desarrollo"


# ------------------------------------------------------------- registro OAuth
def init_auth(app):
    """Configura la BD y los proveedores OAuth sobre la app Flask."""
    cfg = cargar_config_oauth()
    app.secret_key = _clave_de_sesion(cfg)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri_base_datos()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    # Render cierra las conexiones ociosas; sin esto Postgres devuelve
    # "server closed the connection unexpectedly" tras un rato de inactividad.
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

    db.init_app(app)
    oauth.init_app(app)

    g = cfg.get("google", {})
    if g.get("habilitado") and g.get("client_id"):
        oauth.register(
            name="google",
            client_id=g["client_id"],
            client_secret=g["client_secret"],
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    m = cfg.get("microsoft", {})
    if m.get("habilitado") and m.get("client_id"):
        tenant = m.get("tenant", "common")
        oauth.register(
            name="microsoft",
            client_id=m["client_id"],
            client_secret=m["client_secret"],
            server_metadata_url=(
                f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"),
            client_kwargs={"scope": "openid email profile User.Read"},
        )

    with app.app_context():
        db.create_all()
        _migrar_columnas_faltantes()

    app.config["_OAUTH_CFG"] = cfg
    return cfg


def _migrar_columnas_faltantes():
    """Agrega columnas nuevas a una BD ya existente (create_all no altera tablas)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    if "usuarios" not in insp.get_table_names():
        return
    existentes = {c["name"] for c in insp.get_columns("usuarios")}
    # SQLite acepta 'BOOLEAN DEFAULT 0' y 'DATETIME'; Postgres exige FALSE y TIMESTAMP.
    es_postgres = db.engine.dialect.name == "postgresql"
    falso = "FALSE" if es_postgres else "0"
    marca_tiempo = "TIMESTAMP" if es_postgres else "DATETIME"
    nuevas = {
        "recordatorio_30_year": "INTEGER",
        "recordatorio_7_year": "INTEGER",
        "mfa_habilitado": f"BOOLEAN DEFAULT {falso}",
        "intentos_fallidos": "INTEGER DEFAULT 0",
        "bloqueado_hasta": marca_tiempo,
    }
    with db.engine.begin() as con:
        for nombre, tipo in nuevas.items():
            if nombre not in existentes:
                con.execute(text(f"ALTER TABLE usuarios ADD COLUMN {nombre} {tipo}"))


auth_bp = Blueprint("auth", __name__)


def _proveedores_activos() -> dict:
    cfg = current_app.config.get("_OAUTH_CFG", {})
    return {
        "google": bool(cfg.get("google", {}).get("habilitado") and cfg.get("google", {}).get("client_id")),
        "microsoft": bool(cfg.get("microsoft", {}).get("habilitado") and cfg.get("microsoft", {}).get("client_id")),
    }


@auth_bp.get("/auth/<proveedor>")
def login(proveedor):
    if proveedor not in ("google", "microsoft"):
        return "Proveedor no soportado", 404
    if not _proveedores_activos().get(proveedor):
        return redirect(url_for("auth.login_page", error="no_configurado"))
    cliente = oauth.create_client(proveedor)
    redirect_uri = url_for("auth.callback", proveedor=proveedor, _external=True)
    session["next"] = request.args.get("next", "/mi-cuenta")
    return cliente.authorize_redirect(redirect_uri)


@auth_bp.get("/auth/<proveedor>/callback")
def callback(proveedor):
    if proveedor not in ("google", "microsoft"):
        return "Proveedor no soportado", 404
    cliente = oauth.create_client(proveedor)
    token = cliente.authorize_access_token()
    info = token.get("userinfo") or {}
    if not info:
        # Microsoft a veces requiere consultar el endpoint de userinfo
        info = cliente.userinfo()

    proveedor_id = str(info.get("sub") or info.get("oid") or info.get("id") or "")
    email = (info.get("email") or info.get("preferred_username") or "").lower()
    nombre = info.get("name") or email.split("@")[0]
    foto = info.get("picture")

    usuario = None
    if proveedor_id:
        usuario = Usuario.query.filter_by(proveedor=proveedor,
                                          proveedor_id=proveedor_id).first()
    if usuario is None and email:
        usuario = Usuario.query.filter_by(email=email).first()

    if usuario is None:
        usuario = Usuario(proveedor=proveedor, proveedor_id=proveedor_id, email=email)
        db.session.add(usuario)
    usuario.proveedor = proveedor
    usuario.proveedor_id = proveedor_id or usuario.proveedor_id
    usuario.email = email or usuario.email
    usuario.nombre = nombre
    usuario.foto = foto
    usuario.ultimo_acceso = datetime.utcnow()
    db.session.commit()

    # Con segundo factor activo, OAuth por sí solo NO abre sesión: el usuario
    # queda "pendiente" hasta que valide su código en /verificar-mfa.
    if usuario.mfa_habilitado:
        session.pop("uid", None)
        session["uid_pendiente"] = usuario.id
        return redirect(url_for("auth.verificar_mfa"))

    session["uid"] = usuario.id
    destino = session.pop("next", "/mi-cuenta")
    return redirect(destino)


@auth_bp.post("/auth/demo")
def login_demo():
    """Ingreso de prueba local (sin OAuth). Gated por oauth.yaml → demo_local."""
    cfg = current_app.config.get("_OAUTH_CFG", {})
    if not cfg.get("demo_local"):
        return "El ingreso de demostración está desactivado.", 403

    email = (request.form.get("email") or "invitado@demo.local").strip().lower()
    nombre = request.form.get("nombre") or email.split("@")[0].title()

    usuario = Usuario.query.filter_by(email=email).first()
    if usuario is None:
        usuario = Usuario(proveedor="demo", proveedor_id="demo-" + email, email=email)
        db.session.add(usuario)
    usuario.nombre = nombre
    usuario.ultimo_acceso = datetime.utcnow()
    db.session.commit()

    if usuario.mfa_habilitado:
        session.pop("uid", None)
        session["uid_pendiente"] = usuario.id
        return redirect(url_for("auth.verificar_mfa"))

    session["uid"] = usuario.id
    return redirect(request.form.get("next") or "/mi-cuenta")


@auth_bp.get("/logout")
def logout():
    session.pop("uid", None)
    session.pop("uid_pendiente", None)
    return redirect("/")


# ------------------------------------------------------- rutas segundo factor
@auth_bp.get("/verificar-mfa")
def verificar_mfa():
    if usuario_pendiente_mfa() is None:
        return redirect(url_for("login_page"))
    return render_template("verificar_mfa.html")


@auth_bp.post("/api/verificar-codigo-totp")
def verificar_codigo_totp():
    """Promueve la sesión pendiente a sesión real si el código es válido."""
    usuario = usuario_pendiente_mfa()
    if usuario is None:
        return jsonify({"error": "No hay una verificación en curso."}), 401

    if esta_bloqueado(usuario):
        restante = int((usuario.bloqueado_hasta - datetime.utcnow()).total_seconds() // 60) + 1
        return jsonify({"error": f"Demasiados intentos. Reintente en {restante} minutos."}), 429

    codigo = (request.get_json(silent=True) or {}).get("codigo", "")
    if not verificar_totp(usuario, codigo):
        registrar_intento_fallido(usuario)
        return jsonify({"error": "Código incorrecto."}), 401

    limpiar_intentos_fallidos(usuario)
    session.pop("uid_pendiente", None)
    session["uid"] = usuario.id
    return jsonify({"ok": True, "destino": session.pop("next", "/mi-cuenta")})


@auth_bp.post("/api/configurar-2fa")
@login_requerido
def configurar_2fa():
    """Genera un secreto TOTP y su código QR. Nada se persiste hasta confirmarlo."""
    import base64
    import io

    import pyotp
    import qrcode

    usuario = usuario_actual()
    secreto = pyotp.random_base32()
    uri = pyotp.TOTP(secreto).provisioning_uri(
        name=usuario.email or "usuario", issuer_name="Declaración de Renta")

    buffer = io.BytesIO()
    qrcode.make(uri).save(buffer, format="PNG")
    qr = base64.b64encode(buffer.getvalue()).decode()

    # el secreto vive en la sesión hasta que el usuario demuestre que lo escaneó
    session["totp_pendiente"] = secreto
    return jsonify({"qr": f"data:image/png;base64,{qr}", "secreto": secreto})


@auth_bp.post("/api/confirmar-2fa")
@login_requerido
def confirmar_2fa():
    """Activa el 2FA solo si el usuario prueba un código válido del secreto nuevo."""
    import pyotp

    secreto = session.get("totp_pendiente")
    if not secreto:
        return jsonify({"error": "No hay una configuración de 2FA en curso."}), 400

    codigo = (request.get_json(silent=True) or {}).get("codigo", "").strip()
    if not pyotp.TOTP(secreto).verify(codigo, valid_window=1):
        return jsonify({"error": "Código incorrecto. Verifique la hora de su teléfono."}), 401

    usuario = usuario_actual()
    mfa = usuario.mfa or UsuarioMFA(usuario_id=usuario.id)
    codigos = generar_backup_codes()
    mfa.totp_secreto = secreto
    mfa.totp_habilitado = True
    mfa.backup_hashes = json.dumps([_hash_backup(c) for c in codigos])
    usuario.mfa_habilitado = True
    db.session.add(mfa)
    db.session.commit()
    session.pop("totp_pendiente", None)

    # los códigos en claro se devuelven una única vez; solo guardamos sus hashes
    return jsonify({"ok": True, "backup_codes": codigos})


@auth_bp.post("/api/desactivar-2fa")
@login_requerido
def desactivar_2fa():
    """Requiere un código válido: si no, quien robe una sesión podría apagarlo."""
    usuario = usuario_actual()
    if not usuario.mfa_habilitado:
        return jsonify({"error": "El 2FA no está activo."}), 400
    codigo = (request.get_json(silent=True) or {}).get("codigo", "")
    if not verificar_totp(usuario, codigo):
        return jsonify({"error": "Código incorrecto."}), 401

    if usuario.mfa:
        db.session.delete(usuario.mfa)
    usuario.mfa_habilitado = False
    db.session.commit()
    return jsonify({"ok": True})
