"""Medidor propio de visitas (src/medidor.py): sin cookies, sin IP, embudo correcto."""
from webapp import app
from src import medidor
from src.auth import db
from src.medidor import EventoWeb

UA_PC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
UA_CEL = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"


def _limpiar():
    EventoWeb.query.delete()
    db.session.commit()


def test_registra_sin_ip_y_rechaza_bots_y_basura():
    with app.app_context():
        _limpiar()
        assert medidor.registrar({"e": "vista", "r": "/", "ref": "https://mail.google.com/x?y=1",
                                  "utm": {"source": "correo", "campaign": "nov"}}, "181.1.2.3", UA_PC)
        assert not medidor.registrar({"e": "vista", "r": "/"}, "1.1.1.1", "Googlebot/2.1")
        assert not medidor.registrar({"e": "vista", "r": "/"}, "1.1.1.1", "Mozilla/5.0 HeadlessChrome/130")
        assert not medidor.registrar({"e": "vista", "r": "/contadores/lector"}, "1.1.1.1", UA_PC)
        assert not medidor.registrar({"e": "paso", "r": "/contadores/recorrido", "n": 9}, "1.1.1.1", UA_PC)
        assert not medidor.registrar({"e": "clic", "r": "/", "b": "cualquiera"}, "1.1.1.1", UA_PC)
        filas = EventoWeb.query.all()
        assert len(filas) == 1
        f = filas[0]
        assert f.origen == "correo" and f.utm_campaign == "nov" and f.dispositivo == "computador"
        assert f.ref_dominio == "mail.google.com"          # solo el dominio
        assert len(f.unico) == 16
        guardado = " ".join(str(v) for v in vars(f).values())
        assert "181.1.2.3" not in guardado                  # la IP no se guarda
        _limpiar()


def test_embudo_y_origen_del_whatsapp():
    with app.app_context():
        _limpiar()
        ip = "190.2.3.4"
        medidor.registrar({"e": "vista", "r": "/", "utm": {"source": "correo"}}, ip, UA_CEL)
        medidor.registrar({"e": "clic", "r": "/", "b": "ver3d"}, ip, UA_CEL)
        medidor.registrar({"e": "vista", "r": "/contadores/recorrido", "ref": "https://tributando.co/"}, ip, UA_CEL)
        for n in range(1, 7):
            medidor.registrar({"e": "paso", "r": "/contadores/recorrido", "n": n}, ip, UA_CEL)
        medidor.registrar({"e": "clic", "r": "/contadores/recorrido", "b": "whatsapp"}, ip, UA_CEL)
        # otra persona que solo abrió la portada desde Google en computador
        medidor.registrar({"e": "vista", "r": "/", "ref": "https://www.google.com/"}, "200.1.1.1", UA_PC)
        m = medidor.resumen(7)
        assert [n for _, n in m["embudo"]] == [2, 1, 1, 1]
        assert m["disp"] == {"celular": 1, "computador": 1}
        orig = {o["origen"]: o for o in m["por_origen"]}
        assert orig["correo"]["whatsapp"] == 1          # el clic se atribuye a la campaña
        assert orig["google"]["personas"] == 1 and orig["google"]["whatsapp"] == 0
        _limpiar()


def test_api_visita_no_pone_cookie_y_portada_tampoco():
    c = app.test_client()
    r = c.post("/api/visita", data='{"e":"vista","r":"/"}', headers={"User-Agent": UA_PC,
                                                                     "Content-Type": "text/plain"})
    assert r.status_code == 204 and "Set-Cookie" not in r.headers
    r = c.post("/api/visita", data="no es json", headers={"User-Agent": UA_PC})
    assert r.status_code == 204
    for ruta in ("/", "/contadores/recorrido"):
        r = c.get(ruta, headers={"User-Agent": UA_PC})
        assert r.status_code == 200
        assert "session=" not in (r.headers.get("Set-Cookie") or "")
        assert b"/static/js/medidor.js" in r.data
    with app.app_context():
        _limpiar()
