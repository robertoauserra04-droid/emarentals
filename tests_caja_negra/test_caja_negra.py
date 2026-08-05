"""Caja negra (H1): que registre el ciclo completo y que JAMÁS tumbe producción.

La regla del sistema es explícita: *la caja negra jamás debe poder tumbar producción*.
Por eso la mitad de este archivo prueba que cuando falla, no pasa nada.

Se copia tal cual a `tests/` de cada repo. NO depende de fixtures del repo (ni `client`
ni `admin`): 4 repos de la flota no tienen carpeta `tests/` y otros 5 la tienen sin
fixture de cliente HTTP. La prueba de integración es la matriz de curl de la entrega.
"""

import asyncio
import os

import pytest

import observabilidad as caja


@pytest.fixture(autouse=True)
def _caja_temporal(tmp_path, monkeypatch):
    """Cada test escribe en su propia caja negra, aparte de la DB de negocio.

    El doble parche NO es redundante: `caja_negra.py` lee la config EN IMPORT, así que
    `setenv` sola no mueve `_DB_PATH` y los tests escribirían en la caja real del repo.
    El `setenv` sí importa para los módulos que leen el env por su cuenta.
    """
    monkeypatch.setenv("CAJA_NEGRA_DB", str(tmp_path / "caja.db"))
    monkeypatch.setenv("CAJA_NEGRA_ACTIVA", "1")
    cn = _core()
    monkeypatch.setattr(cn, "_DB_PATH", str(tmp_path / "caja.db"))
    monkeypatch.setattr(cn, "_CASOS_DIR", str(tmp_path / "casos"))
    yield


def _core():
    """El core, resuelto por la fachada: vive en la raíz, en `app/` o en `core/`."""
    assert caja._cn is not None, "observabilidad no encontró caja_negra"
    return caja._cn


def _eventos(**filtros):
    return _core().buscar(limite=200, **filtros)


class _HTTPError(Exception):
    """Imita a fastapi.HTTPException para no acoplar el test al framework."""

    def __init__(self, status_code, detail=""):
        self.status_code = status_code
        super().__init__(detail)


# ── Que registre ──────────────────────────────────────────────────────────── #

def test_turno_agrupa_sus_eventos():
    with caja.turno(canal="whatsapp", telefono="5218112345678"):
        caja.registrar("mensaje_entrante", {"texto": "hola"})
        caja.registrar("mensaje_saliente", {"texto": "¡hola!"})
    evs = _eventos()
    assert len({e["turno_id"] for e in evs}) == 1
    assert all(e["canal"] == "whatsapp" for e in evs)


def test_error_dentro_del_turno_queda_registrado():
    with pytest.raises(RuntimeError):
        with caja.turno(canal="whatsapp"):
            raise RuntimeError("truena")
    errores = _eventos(nivel="error")
    assert errores and "truena" in str(errores[-1]["datos"])


def test_decorador_de_panel_registra_entrada_y_resultado():
    @caja.accion_panel("cobro_crear", dinero=True)
    def cobrar(*, monto, db=None):
        return {"id": 1, "monto": monto}

    assert cobrar(monto=550, db="fake") == {"id": 1, "monto": 550}
    acciones = _eventos(evento="accion_panel")
    assert acciones and acciones[-1]["datos"]["accion"] == "cobro_crear"
    assert acciones[-1]["datos"]["params"] == {"monto": 550}   # `db` no se guarda
    metricas = _eventos(evento="metrica")
    assert metricas and metricas[-1]["datos"]["monto"] == 550


def test_decorador_sirve_en_async():
    """Un `async def` decorado NO debe registrar el objeto coroutine como resultado."""
    @caja.accion_panel("cobro_crear_async", dinero=True)
    async def cobrar(*, monto):
        return {"id": 2, "monto": monto}

    assert asyncio.run(cobrar(monto=310)) == {"id": 2, "monto": 310}
    acciones = _eventos(evento="accion_panel")
    assert acciones and "coroutine" not in str(acciones[-1]["datos"])
    assert _eventos(evento="metrica")[-1]["datos"]["monto"] == 310


def test_reusa_turno_ambiente():
    """Un handler decorado bajo un turno abierto (el del middleware) NO abre otro.

    Si esto falla, el timeline de un request queda partido en dos turno_id y el visor
    del panel muestra la mitad de la historia. Es la regresión que aparece si alguien
    convierte el middleware ASGI en un BaseHTTPMiddleware.
    """
    @caja.accion_panel("cita_crear")
    def crear(*, cuando):
        return {"ok": True}

    with caja.turno(canal="panel") as tid:
        crear(cuando="mañana")
        caja.registrar("mensaje_saliente", {"texto": "listo"})

    evs = _eventos(turno_id=tid)
    assert {e["turno_id"] for e in evs} == {tid}
    assert "accion_ejecutada" in [e["evento"] for e in evs]


def test_monto_se_lee_de_la_entrada_cuando_el_resultado_no_lo_trae():
    """Un borrado devuelve {"ok": true}: el monto solo está en la entrada."""
    @caja.accion_panel("cobro_anular", dinero=True)
    def anular(*, monto, id):
        return {"ok": True}

    anular(monto=300, id=7)
    assert _eventos(evento="metrica")[-1]["datos"]["monto"] == 300


def test_un_rechazo_4xx_no_cuenta_como_error():
    """Un 404 es el sistema funcionando. Si contara como error, el agente de robustez
    reportaría alarmas falsas en cada "no existe"."""
    @caja.accion_panel("cobro_borrar")
    def borrar(*, id):
        raise _HTTPError(404, "no existe")

    antes = len(_eventos(nivel="error"))
    with pytest.raises(_HTTPError):
        borrar(id=9)
    assert len(_eventos(nivel="error")) == antes
    avisos = [e for e in _eventos(nivel="warn") if e["datos"].get("accion") == "cobro_borrar"]
    assert avisos and avisos[-1]["datos"]["status"] == 404


def test_una_falla_real_si_cuenta_como_error():
    @caja.accion_panel("cobro_roto")
    def roto(*, id):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        roto(id=1)
    errores = _eventos(nivel="error")
    assert any(e["evento"] == "error" and "boom" in str(e["datos"].get("traceback", ""))
               for e in errores)


# ── Que no estorbe ────────────────────────────────────────────────────────── #

def test_apagada_no_registra_pero_todo_funciona(monkeypatch):
    monkeypatch.setenv("CAJA_NEGRA_ACTIVA", "0")

    @caja.accion_panel("cobro_crear")
    def cobrar(*, monto):
        return 5

    assert cobrar(monto=1) == 5
    assert _eventos() == []


def test_un_fallo_de_la_caja_no_rompe_el_handler(monkeypatch):
    """Si la caja negra misma truena, el handler devuelve igual. Esta es LA prueba."""
    cn = _core()

    def _explota(*a, **k):
        raise OSError("disco lleno")

    monkeypatch.setattr(cn, "_conn", _explota)

    @caja.accion_panel("cobro_crear", dinero=True)
    def cobrar(*, monto):
        return {"monto": monto}

    assert cobrar(monto=99) == {"monto": 99}


def test_datos_enormes_se_truncan():
    with caja.turno(canal="panel"):
        caja.registrar("accion_panel", {"basura": "x" * 200_000})
    assert len(str(_eventos()[-1]["datos"])) < 100_000


def test_la_caja_negra_no_toca_la_db_de_negocio():
    """La caja vive en su propio SQLite: nunca la base del cliente."""
    assert "caja" not in os.getenv("DATABASE_URL", "")
    assert os.environ["CAJA_NEGRA_DB"] != os.getenv("DATABASE_URL", "")


# ── El resumen que consumen el panel y el agente de robustez ──────────────── #

def test_resumen_cumple_el_contrato_del_agente_de_robustez():
    cn = _core()

    with caja.turno(canal="panel"):
        caja.registrar("accion_panel", {"accion": "POST /api/cobros", "duracion_ms": 4200})
    with pytest.raises(RuntimeError):
        with caja.turno(canal="panel"):
            raise RuntimeError("boom")

    r = cn.resumen(24)
    assert {"instrumentada", "eventos_total", "n_errores", "lat_max_ms", "errores"} <= set(r)
    assert r["instrumentada"] is True
    assert r["n_errores"] == 1
    assert r["lat_max_ms"] >= 4200        # toma duracion_ms, no solo lat_ms


def test_nivel_warning_se_normaliza_a_warn():
    """`"warning"` no está en el vocabulario y quedaba invisible a buscar(nivel="warn")."""
    with caja.turno(canal="panel"):
        caja.registrar("accion_panel", {"accion": "POST /x"}, nivel="warning")
    assert len(_eventos(nivel="warn")) == 1
