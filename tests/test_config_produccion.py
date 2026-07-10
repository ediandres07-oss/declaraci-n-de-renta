"""Configuración de producción: base de datos Postgres y clave de sesión."""
import pytest

from src.auth import _clave_de_sesion, uri_base_datos

CLAVE_DEV = "clave-temporal-de-desarrollo"


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    """Aísla las pruebas de las variables de entorno de la máquina."""
    for var in ("DATABASE_URL", "SECRET_KEY", "RENDER"):
        monkeypatch.delenv(var, raising=False)


# ------------------- URI de la base de datos --------------------------------

def test_sin_database_url_usa_sqlite_local():
    uri = uri_base_datos()
    assert uri.startswith("sqlite:///")
    assert uri.endswith("sessions/usuarios.db")


def test_database_url_de_render_se_normaliza_a_postgresql(monkeypatch):
    """Render entrega 'postgres://', esquema que SQLAlchemy 2 ya no acepta."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert uri_base_datos() == "postgresql://u:p@host:5432/db"


def test_database_url_ya_normalizada_no_se_toca(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    assert uri_base_datos() == "postgresql://u:p@host:5432/db"


def test_solo_se_reemplaza_el_prefijo(monkeypatch):
    """Una contraseña que contenga 'postgres://' no debe corromperse."""
    monkeypatch.setenv("DATABASE_URL", "postgres://u:postgres%3A%2F%2Fx@host/db")
    assert uri_base_datos() == "postgresql://u:postgres%3A%2F%2Fx@host/db"


def test_database_url_vacia_cae_en_sqlite(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert uri_base_datos().startswith("sqlite:///")


# ------------------- clave de sesión ----------------------------------------

def test_local_sin_clave_usa_la_de_desarrollo():
    assert _clave_de_sesion({}) == CLAVE_DEV


def test_la_variable_de_entorno_gana_sobre_el_yaml(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "desde-el-entorno")
    assert _clave_de_sesion({"secret_key": "desde-el-yaml"}) == "desde-el-entorno"


def test_sin_entorno_usa_el_yaml():
    assert _clave_de_sesion({"secret_key": "desde-el-yaml"}) == "desde-el-yaml"


@pytest.mark.parametrize("marca", ["RENDER", "DATABASE_URL"])
def test_en_produccion_sin_clave_no_arranca(monkeypatch, marca):
    """La constante de desarrollo es pública: nunca debe usarse en producción."""
    monkeypatch.setenv(marca, "1")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _clave_de_sesion({})


def test_en_produccion_con_clave_arranca(monkeypatch):
    monkeypatch.setenv("RENDER", "1")
    monkeypatch.setenv("SECRET_KEY", "clave-larga-y-aleatoria")
    assert _clave_de_sesion({}) == "clave-larga-y-aleatoria"


def test_produccion_nunca_devuelve_la_clave_de_desarrollo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    monkeypatch.setenv("SECRET_KEY", "otra-clave")
    assert _clave_de_sesion({}) != CLAVE_DEV


# ------------------- endpoint de salud --------------------------------------

def test_salud_reporta_motor_y_conexion():
    from webapp import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/salud")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert j["motor"] in ("sqlite", "postgresql")
    assert j["anio_gravable"] == 2025


def test_salud_no_filtra_credenciales():
    from webapp import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        cuerpo = c.get("/api/salud").get_data(as_text=True).lower()
    for fuga in ("password", "usuarios.db", "@", "sqlite:///", "postgresql://"):
        assert fuga not in cuerpo
