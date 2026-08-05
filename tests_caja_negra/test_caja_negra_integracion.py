"""Caja negra (H1) — integración: middleware + decorador + router fleet.

Prueba lo que la suite unitaria no puede: que el turno del middleware propague al
endpoint (sync y async), que un request = un turno, que la contraseña NO entre, que un
401 de dependencia quede registrado (algo que un decorador nunca puede ver) y que los
dos contratos de consumidor respondan lo que el panel y el agente de robustez esperan.

Se copia tal cual a `tests/` de cada repo: monta su propia app FastAPI de juguete, así
que no depende de nada del repo (ni fixtures, ni DB, ni auth). Los módulos de la caja se
resuelven donde vivan, igual que hace `observabilidad.py`.
"""
import importlib
import pathlib
import sys

import pytest
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel


def _es_router_de_caja(mod) -> bool:
    """¿Este módulo es de verdad el drop-in de la caja negra?

    Hace falta porque un nombre puede estar ocupado por otra cosa: en vellapanel
    `app/routers/fleet.py` es el PROXY del visor de la flota (prefijo `/api/fleet`),
    no este drop-in, y sin esta comprobación el test montaba el router equivocado y
    todo respondía 404.
    """
    return "caja-negra" in getattr(getattr(mod, "router", None), "prefix", "")


def _resolver(*rutas, validar=None):
    """Importa el primero que exista Y pase `validar`: los repos ponen estos archivos
    en sitios distintos.

    Si el import por paquete falla, se carga por RUTA DE ARCHIVO: en varios repos el
    `routes/__init__.py` arrastra medio proyecto (Google API, asyncpg…) y el import se
    caería por una dependencia que no tiene nada que ver con la caja negra.
    """
    def _ok(mod):
        return mod is not None and (validar is None or validar(mod))

    for r in rutas:
        try:
            mod = importlib.import_module(r)
        except Exception:  # noqa: BLE001
            continue
        if _ok(mod):
            return mod
    import importlib.util
    for r in rutas:
        f = pathlib.Path(r.replace(".", "/") + ".py")
        if not f.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location(r.replace(".", "_"), f)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            continue
        if _ok(mod):
            return mod
    pytest.skip(f"no se encontró ninguno de {rutas}")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CAJA_NEGRA_DB", str(tmp_path / "caja.db"))
    monkeypatch.setenv("CAJA_NEGRA_CASOS_DIR", str(tmp_path / "casos"))
    monkeypatch.setenv("CAJA_NEGRA_ACTIVA", "1")
    monkeypatch.setenv("FLEET_INGEST_KEY", "llave-de-prueba")
    import observabilidad as _caja
    cn = _caja._cn
    assert cn is not None, "observabilidad no encontró caja_negra"
    monkeypatch.setattr(cn, "_DB_PATH", str(tmp_path / "caja.db"))
    monkeypatch.setattr(cn, "_CASOS_DIR", str(tmp_path / "casos"))

    import observabilidad as caja
    # Los repos ponen el router en sitios distintos según su layout; se prueban todos.
    fleet = _resolver("routes.fleet", "app.routers.fleet", "app.routes.fleet",
                      "app.api.fleet", "api.fleet", "api.routes.fleet", "core.routes.fleet",
                      "core.fleet_caja", "app.fleet_caja", "fleet_caja", "fleet_caja_negra",
                      "app.routers.fleet_caja",
                      validar=_es_router_de_caja)
    mw = _resolver("caja_negra_mw", "app.caja_negra_mw", "core.caja_negra_mw",
                   "utils.caja_negra_mw")
    CajaNegraMiddleware = mw.CajaNegraMiddleware

    api = APIRouter()

    class Cobro(BaseModel):
        monto: float
        servicio: str = ""

    class Login(BaseModel):
        username: str
        password: str

    def token_falso(authorization: str = Header(default="")):
        return {"sub": "7"} if authorization else None

    @api.post("/api/login")
    def login(body: Login):
        return {"access_token": "x"}

    @api.post("/api/cobros")
    @caja.accion_panel("cobro_crear", dinero=True)
    def crear_cobro(*, body: Cobro, token=Depends(token_falso)):
        if token is None:
            raise HTTPException(401, "sin token")
        return {"id": 1, "monto": body.monto}

    @api.delete("/api/cobros/{cid}")
    @caja.accion_panel("cobro_anular", dinero=True)
    async def anular(*, cid: int, monto: float, token=Depends(token_falso)):
        return {"ok": True}

    @api.post("/api/rompe")
    def rompe():
        raise RuntimeError("boom de verdad")

    @api.get("/api/listado")
    def listado():
        return []

    @api.post("/whatsapp")
    def webhook(body: dict):
        with caja.turno(canal="whatsapp", telefono="5218110000000"):
            caja.registrar("mensaje_entrante", {"texto": body.get("text")})
            caja.registrar("mensaje_saliente", {"texto": "respuesta"})
        return {"ok": True}

    app = FastAPI()
    app.include_router(api)
    app.include_router(fleet.router)
    app.add_middleware(CajaNegraMiddleware)
    return app


K = {"X-Fleet-Key": "llave-de-prueba"}

# Un JWT de verdad: el resolver decodifica el claim `sub` sin verificar firma (la app ya
# validó el token con su propia dependencia; aquí solo se etiqueta la línea de log).
import jwt as _pyjwt
_TOK = _pyjwt.encode({"sub": "7", "rol": "admin"}, "cualquier-secreto", algorithm="HS256")
AUTH = {"authorization": f"Bearer {_TOK}"}


def _ev(**f):
    import observabilidad as _caja
    return _caja._cn.buscar(limite=300, **f)


def test_un_request_un_turno_sync(app):
    c = TestClient(app)
    assert c.post("/api/cobros", json={"monto": 550}, headers=AUTH).status_code == 200
    ap = _ev(evento="accion_panel")
    ae = _ev(evento="accion_ejecutada")
    me = _ev(evento="metrica")
    assert ap and ae and me, (ap, ae, me)
    # El contextvar del middleware llegó al endpoint sync (threadpool copia el contexto).
    assert ap[-1]["turno_id"] == ae[-1]["turno_id"] == me[-1]["turno_id"]
    d = ap[-1]["datos"]
    assert d["accion"] == "POST /api/cobros" and d["status"] == 200
    # El body CRUDO, tal como entró: sin los defaults que Pydantic rellena después.
    assert d["params"] == {"monto": 550}
    # Del scope, no de los kwargs. Qué se espera depende de la variante de
    # `resolver_usuario` de este repo: la de JWT saca el claim `sub` del bearer que
    # manda esta prueba; la de cookie de sesión devuelve None porque esta app de
    # juguete no monta SessionMiddleware (y eso es correcto, no un fallo).
    import ast as _ast
    import inspect

    import observabilidad as _caja
    # El CUERPO, sin el docstring: ese enumera las cuatro variantes y nombra todas.
    _fn = _ast.parse(inspect.getsource(_caja.resolver_usuario).lstrip()).body[0]
    _cuerpo = [n for n in _fn.body
               if not (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant))]
    fuente = "\n".join(_ast.unparse(n) for n in _cuerpo)
    if "_de_jwt" in fuente:
        assert d["usuario"] == "7", d["usuario"]
    elif "anonimo" in fuente:
        assert d["usuario"] == "anonimo", d["usuario"]
    else:
        assert "usuario" in d
    assert d["origen"] == "middleware"
    assert ae[-1]["datos"]["accion"] == "cobro_crear"


def test_un_request_un_turno_async(app):
    c = TestClient(app)
    assert c.delete("/api/cobros/9?monto=300", headers=AUTH).status_code == 200
    ap, ae, me = _ev(evento="accion_panel"), _ev(evento="accion_ejecutada"), _ev(evento="metrica")
    assert ap[-1]["turno_id"] == ae[-1]["turno_id"] == me[-1]["turno_id"]
    assert me[-1]["datos"]["monto"] == 300      # de la entrada: el delete no lo devuelve
    assert "coroutine" not in str(ae[-1]["datos"])


def test_la_contrasena_no_entra_a_la_caja(app):
    c = TestClient(app)
    c.post("/api/login", json={"username": "admin", "password": "hunter2-secreto"})
    todo = str([e["datos"] for e in _ev()])
    assert "hunter2-secreto" not in todo, todo
    import observabilidad as _caja
    with open(_caja._cn._DB_PATH, "rb") as f:
        assert b"hunter2-secreto" not in f.read()


def test_422_es_warn_y_dice_por_que_fallo(app):
    c = TestClient(app)
    r = c.post("/api/cobros", json={"monto": "no-es-numero"}, headers=AUTH)
    assert r.status_code == 422
    ap = _ev(evento="accion_panel")[-1]
    assert ap["nivel"] == "warn", ap["nivel"]
    assert "por_que_fallo" in ap["datos"]
    # El decorador nunca corrió (Pydantic rechazó antes): solo el middleware lo vio.
    assert not _ev(evento="accion_ejecutada")


def test_401_de_dependencia_queda_registrado(app):
    c = TestClient(app)
    assert c.post("/api/cobros", json={"monto": 1}).status_code == 401
    ap = _ev(evento="accion_panel")[-1]
    assert ap["nivel"] == "warn" and ap["datos"]["status"] == 401
    assert ap["datos"]["autenticado"] is False


def test_500_es_error_y_congela_un_caso(app):
    c = TestClient(app)
    with pytest.raises(RuntimeError):
        c.post("/api/rompe", json={})
    ap = _ev(evento="accion_panel")[-1]
    assert ap["nivel"] == "error", ap        # antes un 500 real se guardaba como info
    assert ap["datos"]["status"] == 500
    assert _ev(nivel="error", evento="error")
    r = c.get("/api/fleet/caja-negra/casos", headers=K)
    assert r.json()["n_casos"] >= 1, r.json()


def test_los_get_no_se_registran(app):
    c = TestClient(app)
    c.get("/api/listado")
    assert _ev() == []


def test_el_webhook_no_lo_toca_el_middleware(app):
    c = TestClient(app)
    c.post("/whatsapp", json={"text": "hola"})
    evs = _ev()
    assert {e["evento"] for e in evs} == {"mensaje_entrante", "mensaje_saliente"}
    assert len({e["turno_id"] for e in evs}) == 1
    assert all(e["canal"] == "whatsapp" for e in evs)


def test_contrato_del_visor_del_panel(app):
    c = TestClient(app)
    c.post("/api/cobros", json={"monto": 550}, headers=AUTH)
    for p in ("/turnos", "/casos", "/resumen"):
        assert c.get(f"/api/fleet/caja-negra{p}", headers=K).status_code == 200, p
    t = c.get("/api/fleet/caja-negra/turnos", headers=K).json()
    assert t["n_turnos"] >= 1 and t["turnos"][0]["primer_mensaje"]
    tid = t["turnos"][0]["turno_id"]
    d = c.get(f"/api/fleet/caja-negra/turno/{tid}", headers=K).json()
    assert d["n_eventos"] >= 2 and d["eventos"][0]["datos"]


def test_fleet_invisible_sin_llave(app):
    c = TestClient(app)
    assert c.get("/api/fleet/caja-negra/turnos").status_code == 404
    assert c.get("/api/fleet/caja-negra/turnos", headers={"X-Fleet-Key": "mala"}).status_code == 404
    assert c.get("/api/fleet/caja-negra/casos/../../caja.db", headers=K).status_code == 404
    assert "fleet/caja-negra" not in str(c.get("/openapi.json").json())


def test_leer_la_caja_no_escribe_en_la_caja(app):
    c = TestClient(app)
    c.post("/api/cobros", json={"monto": 5}, headers=AUTH)
    n = len(_ev())
    for p in ("/turnos", "/casos", "/resumen"):
        c.get(f"/api/fleet/caja-negra{p}", headers=K)
    assert len(_ev()) == n
