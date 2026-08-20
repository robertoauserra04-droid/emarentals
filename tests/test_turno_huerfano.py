"""Un evento suelto no debe hacer creer al resto del proceso que hay un turno abierto.

El core es tolerante a propósito: si alguien llama `registrar()` sin turno abierto, en vez de
tirar el evento se inventa uno huérfano con `turno_id="sin-turno"`. El problema es que además
**lo deja pegado en el contextvar**, y no lo resetea nunca. Desde ese momento, preguntar
`_turno_ctx.get() is not None` da True para siempre aunque no haya ningún turno real.

Qué se rompía con eso: `@accion_panel` distingue dos situaciones a propósito —dentro del turno
de un request emite `accion_ejecutada`, fuera de él emite `accion_panel`— para que el visor no
vea tres formas del mismo evento. Con el contextvar contaminado, un handler que corre fuera de
un request (un job, un comando de consola, un test) emitía la forma de "dentro del request" y
sus eventos quedaban archivados bajo el turno de relleno.

Se descubrió porque dos tests del drop-in `tests_caja_negra/` fallaban SOLO al correrse junto
con esta suite: la de negocio deja un evento suelto y contaminaba a la siguiente.

Este archivo vive en `tests/` y no en `tests_caja_negra/` a propósito: esa carpeta se copia tal
cual desde la plantilla de la flota y no se edita por repo.
"""
import observabilidad as caja


def _reset():
    """Deja el contextvar como al arrancar el proceso."""
    if caja._cn is not None:
        caja._cn._turno_ctx.set(None)


def test_sin_nada_no_hay_turno():
    _reset()
    assert caja.turno_actual() is None


def test_un_evento_suelto_no_inventa_un_turno():
    """El corazón del arreglo. Antes esto devolvía 'sin-turno' y contaminaba todo lo demás."""
    _reset()
    caja.registrar("prueba_evento_suelto", {"de": "un job sin turno"})
    assert caja.turno_actual() is None


def test_un_turno_de_verdad_si_se_ve():
    _reset()
    with caja.turno(canal="whatsapp", telefono="5218112345678") as tid:
        if tid is not None:            # con la caja apagada el turno es no-op
            assert caja.turno_actual() == tid
    assert caja.turno_actual() is None


def test_el_relleno_no_cuenta_como_turno_heredado():
    """`_turno_real` es lo que consulta `_turno_o_actual` para decidir si hereda."""
    assert caja._turno_real({"turno_id": "sin-turno"}) is False
    assert caja._turno_real({"turno_id": "abc123"}) is True
    assert caja._turno_real(None) is False
    assert caja._turno_real({}) is False


def test_accion_de_panel_fuera_de_un_request_se_marca_como_tal():
    """El síntoma completo, punta a punta: un evento suelto ANTES no debe cambiar la forma
    del evento que emite el decorador después."""
    if not caja.activa():
        return                          # sin caja negra no hay nada que comprobar
    _reset()
    caja.registrar("prueba_evento_suelto", {"de": "un job sin turno"})

    @caja.accion_panel("prueba_accion")
    def handler(*, valor):
        return {"ok": valor}

    assert handler(valor=1) == {"ok": 1}
    eventos = caja._cn.buscar(limite=50, evento="accion_panel")
    assert any(e["datos"].get("accion") == "prueba_accion" for e in eventos)
