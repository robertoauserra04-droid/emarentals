"""Fachada de la caja negra (H1) — el ÚNICO punto por el que el repo la usa.

`caja_negra.py` es la plantilla del sistema, copiada tal cual y sin tocar. Este módulo
la envuelve para cuatro cosas:

1. **Que su ausencia nunca sea un problema.** Si el módulo falta o la caja está
   apagada, todo aquí queda no-op: el resto del código llama igual y no hace falta un
   `if caja:` regado. *La caja negra jamás debe poder tumbar producción.*
2. **Resolver dónde vive el core** (raíz, `app/`, `core/`, `utils/`). Por eso los otros
   tres archivos de la caja (`caja_negra_mw.py`, el router fleet y el endpoint de
   robustez) son idénticos byte a byte en toda la flota: importan de aquí, no del core.
3. **Un decorador para el panel** (`@accion_panel`), para no regar `registrar()`.
4. **Resolver QUIÉN hizo la acción** con un solo mecanismo, pese a que la flota tiene
   ocho estilos de auth distintos (ver `resolver_usuario` abajo).

ESTE ES EL ÚNICO ARCHIVO DE LA CAJA QUE SE EDITA POR REPO, y solo en dos sitios:
`CLIENTE` (el slug) y el cuerpo de `resolver_usuario`. Todo lo demás se copia igual.

Se apaga entera con `CAJA_NEGRA_ACTIVA=0`, en caliente (se relee el env en cada llamada).
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import os
from contextlib import contextmanager

# ── EDITAR POR REPO (1 de 2): el slug del cliente ────────────────────────────────
# Debe coincidir con el `slug` de la fila en la pestaña 🛡 del panel, o el visor de
# caja negra sale vacío: vellapanel/app/routers/fleet.py resuelve la URL por slug.
CLIENTE = os.getenv("CAJA_NEGRA_CLIENTE") or os.getenv("VELLA_CLIENTE") or "emarentals"

# ── Resolución tolerante del core ────────────────────────────────────────────────
# Un repo plano lo tiene en la raíz; los de paquete en `app/`; los colegios en `core/`.
_cn = None
for _ruta in ("caja_negra", "app.caja_negra", "core.caja_negra", "utils.caja_negra"):
    try:
        _cn = __import__(_ruta, fromlist=["*"])
        break
    except Exception:  # noqa: BLE001 — sin core, todo lo de abajo es no-op
        continue


def activa() -> bool:
    return _cn is not None and os.getenv("CAJA_NEGRA_ACTIVA", "1") != "0"


# ── Quién hizo la acción ─────────────────────────────────────────────────────────
# El middleware resuelve el usuario del `scope` de ASGI (no de los kwargs del handler)
# y lo deja aquí; el decorador lo lee. Se resuelve del scope porque es lo único que los
# ocho estilos de auth de la flota tienen en común, y porque en los repos donde el auth
# está en el `include_router(..., dependencies=[...])` el handler no recibe token.
_usuario_ctx: contextvars.ContextVar = contextvars.ContextVar("caja_negra_usuario", default=None)


def set_usuario(usuario) -> None:
    try:
        _usuario_ctx.set(usuario)
    except Exception:  # noqa: BLE001
        pass


def usuario_actual():
    try:
        return _usuario_ctx.get()
    except Exception:  # noqa: BLE001
        return None


def _bearer(scope) -> str | None:
    """Saca el token Bearer de los headers crudos del scope ASGI."""
    for k, v in (scope.get("headers") or []):
        if k == b"authorization":
            s = v.decode("latin-1", errors="replace")
            return s[7:].strip() if s[:7].lower() == "bearer " else s.strip()
    return None


def _de_jwt(scope):
    """Variante A — bearer JWT. Lee el claim `sub` SIN verificar la firma.

    Es correcto y seguro aquí: la app ya validó el token con su propia dependencia,
    esto corre después de la respuesta y solo etiqueta una línea de log — no autoriza
    nada. Además evita necesitar el SECRET_KEY de cada repo (viven en 18 sitios) y
    funciona igual si el `verificar_token` del repo devuelve dict, str u objeto ORM.
    """
    tok = _bearer(scope)
    if not tok:
        return None
    try:
        import jwt  # PyJWT
        p = jwt.decode(tok, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        try:
            from jose import jwt as _jose  # python-jose
            p = _jose.get_unverified_claims(tok)
        except Exception:  # noqa: BLE001
            return None
    return str(p.get("sub") or p.get("user_id") or p.get("username") or p.get("uid") or "") or None


def _de_sesion(scope):
    """Variante B — cookie de sesión de Starlette (los colegios).

    Funciona aunque este middleware quede por fuera de `SessionMiddleware`: ese muta
    el MISMO dict `scope`, y aquí se resuelve después de que corrió la app.
    """
    s = scope.get("session") or {}
    uid = s.get("userId") or s.get("user_id") or s.get("id")
    if not uid:
        return None
    nombre = s.get("userName") or s.get("username") or ""
    return f"{uid}:{nombre}" if nombre else str(uid)


# ── EDITAR POR REPO (2 de 2): cómo se identifica al usuario ──────────────────────
def resolver_usuario(scope):
    """Devuelve un identificador de quién hizo la acción, o None. Nunca lanza.

    Variantes de la flota — dejar la que aplique:
      A) bearer JWT con claim `sub`  → `return _de_jwt(scope)`         (16 repos)
      B) cookie de sesión Starlette  → `return _de_sesion(scope)`      (los colegios)
      C) el repo no tiene auth       → `return "anonimo"`              (AgenciaViajes)
      D) combinada (default)         → A, luego B, luego None
    """
    try:
        return _de_jwt(scope)      # variante A: EMA Rentals usa bearer JWT (OAuth2PasswordBearer)
    except Exception:  # noqa: BLE001
        return None


# ── API básica ───────────────────────────────────────────────────────────────────
@contextmanager
def turno(canal: str = "whatsapp", telefono: str | None = None, cliente: str | None = None):
    """Abre un turno. Sin caja negra, es un `with` que no hace nada."""
    if not activa():
        yield None
        return
    with _cn.turno(cliente=cliente or CLIENTE, canal=canal, telefono=telefono) as tid:
        yield tid


@contextmanager
def _turno_o_actual(canal: str = "panel"):
    """Reusa el turno que ya esté abierto (el del middleware) en vez de anidar uno.

    Sin esto, un handler decorado que corre bajo el middleware produce DOS turno_id
    para un solo request y el timeline queda partido en dos: `caja_negra.turno()` abre
    turno nuevo sin condición. Cede True si el turno es nuestro, False si lo heredamos.
    """
    if not activa():
        yield None
        return
    try:
        heredado = _cn._turno_ctx.get() is not None
    except Exception:  # noqa: BLE001
        heredado = False
    if heredado:
        yield False
        return
    # Turno propio abierto a mano (no con `_cn.turno()`) a propósito: ese registra un
    # evento `error` por cada excepción que pasa, y aquí el decorador ya decide si el
    # fallo fue una falla real o un rechazo 4xx. Así no se duplica ni se infla.
    token = None
    try:
        token = _cn._turno_ctx.set(None)
        _cn.iniciar_turno(cliente=CLIENTE, canal=canal)
    except Exception:  # noqa: BLE001
        pass
    try:
        yield True
    finally:
        try:
            if token is not None:
                _cn._turno_ctx.reset(token)
        except Exception:  # noqa: BLE001
            pass


def iniciar_turno(canal: str = "panel", telefono: str | None = None,
                  cliente: str | None = None) -> str | None:
    """Abre un turno SIN context manager y lo deja en el contexto de ESTA tarea.

    Lo usa el middleware ASGI: necesita que el turno siga abierto cuando ceda el control
    a la app, así que no puede envolverlo en un `with`. Devuelve el turno_id o None.
    """
    if not activa():
        return None
    try:
        return _cn.iniciar_turno(cliente=cliente or CLIENTE, canal=canal, telefono=telefono)
    except Exception:  # noqa: BLE001
        return None


def turno_actual() -> str | None:
    """El turno_id abierto ahora (normalmente el que abrió el middleware), o None.

    Existe para que nadie tenga que meter mano a `_cn._turno_ctx`, que es API privada:
    los repos que lo hacían se rompen en silencio si el core cambia por dentro.
    """
    if not activa():
        return None
    try:
        return (_cn._turno_ctx.get() or {}).get("turno_id")
    except Exception:  # noqa: BLE001
        return None


def registrar(evento: str, datos=None, nivel: str = "info") -> None:
    if activa():
        _cn.registrar(evento, datos, nivel=nivel)


def capturar_error(exc: BaseException | None = None, datos=None) -> None:
    if activa():
        _cn.capturar_error(exc, datos)


def metrica(datos: dict) -> None:
    """Eventos de dinero. Son los que SIEMPRE importan al 100%."""
    registrar("metrica", datos)


def archivar_caso(turno_id: str, **kw) -> str | None:
    if not activa() or not turno_id:
        return None
    try:
        return _cn.archivar_caso(turno_id, **kw)
    except Exception:  # noqa: BLE001
        return None


# ── El decorador del panel ───────────────────────────────────────────────────────
_IGNORAR_KWARGS = ("db", "request", "response", "token", "background_tasks",
                   "current_user", "usuario_actual", "session")


def _entrada(kwargs: dict) -> dict:
    return {k: v for k, v in kwargs.items() if k not in _IGNORAR_KWARGS}


def _usuario_de_kwargs(kwargs: dict):
    """Respaldo para cuando el middleware no corrió (CLI, APScheduler, test unitario)."""
    for clave in ("token", "current_user", "user", "usuario", "usuario_actual"):
        v = kwargs.get(clave)
        if v is None:
            continue
        if isinstance(v, dict):
            s = v.get("sub") or v.get("username") or v.get("user_id") or v.get("id")
            if s:
                return str(s)
        elif isinstance(v, str):
            return v
        else:
            for attr in ("email", "username", "usuario", "id"):
                s = getattr(v, attr, None)
                if s:
                    return str(s)
    return None


_CLAVES_MONTO = ("monto", "total", "importe", "monto_total", "monto_servicio",
                 "precio", "cantidad_pagada")


def _monto_plano(fuente) -> float | None:
    if not isinstance(fuente, dict):
        return None
    for clave in _CLAVES_MONTO:
        v = fuente.get(clave)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                pass
    return None


def _monto(entrada: dict, resultado):
    """El monto sale del resultado o de la entrada, en ese orden, y un nivel adentro.

    Leerlo solo del retorno dejaba en None todo lo que borra o anula (un
    `DELETE /cobros/{id}` devuelve `{"ok": true}`), que es justo el movimiento que más
    importa auditar. Y hay que mirar anidado porque media flota declara los handlers
    como `def crear(body: dict = Body(...))`: ahí el monto vive en `entrada["body"]`.
    """
    for fuente in (resultado if isinstance(resultado, dict) else None, entrada):
        m = _monto_plano(fuente)
        if m is not None:
            return m
        if isinstance(fuente, dict):
            for v in fuente.values():
                m = _monto_plano(v)
                if m is not None:
                    return m
    return None


def _es_rechazo(exc: BaseException) -> bool:
    """¿Es un rechazo de negocio (HTTPException 4xx) y no una falla del sistema?

    Importa para no inflar el contador de errores: `resumen()` lo reporta al agente de
    robustez, que trata `n_errores > 0` como alarma. Un 404 "no existe" o un 403 son el
    sistema funcionando; solo los 5xx y las excepciones no controladas son errores.
    """
    code = getattr(exc, "status_code", None)
    return isinstance(code, int) and 400 <= code < 500


def accion_panel(nombre: str, dinero: bool = False):
    """Decorador para handlers del panel que ESCRIBEN. Sirve en sync y en async.

    Qué registra, y por qué así:

    • Si ya hay turno abierto (el caso normal: el middleware abrió uno para el request)
      emite `accion_ejecutada` con {accion, usuario, params, antes, despues}. El
      `accion_panel` del request lo emite el middleware, uno solo por request, para que
      los consumidores no vean tres formas del mismo evento.
    • Si NO hay turno (CLI, job de APScheduler, test unitario) emite `accion_panel`
      con las mismas claves, porque ahí no hay middleware que lo haga.
    • Con `dinero=True` emite además una `metrica`, incluso si no se pudo determinar el
      monto (marcada con `monto_desconocido`), para que se vea que la ruta de dinero corrió.

    Va DESPUÉS de que el handler devuelve: los intentos que fallaron por validación
    salen como `error`, no como acción ejecutada.
    """
    def deco(fn):
        def _registrar(entrada, usuario, resultado, propio):
            evento = "accion_panel" if propio else "accion_ejecutada"
            registrar(evento, {"accion": nombre, "usuario": usuario, "params": entrada,
                               "antes": None, "despues": resultado,
                               "origen": "decorador"})
            if dinero:
                m = _monto(entrada, resultado)
                datos = {"accion": nombre, "monto": m, "usuario": usuario}
                if m is None:
                    datos["monto_desconocido"] = True
                metrica(datos)

        def _contexto(kwargs):
            return _entrada(kwargs), (usuario_actual() or _usuario_de_kwargs(kwargs))

        def _fallo(exc, entrada, usuario, propio):
            """Registra el intento fallido. Un 4xx es rechazo (warn), lo demás error."""
            rechazo = _es_rechazo(exc)
            registrar("accion_ejecutada",
                      {"accion": nombre, "usuario": usuario, "params": entrada,
                       "fallo": True, "motivo": str(exc)[:500],
                       "status": getattr(exc, "status_code", None),
                       "origen": "decorador"},
                      nivel="warn" if rechazo else "error")
            # El evento `error` con traceback, solo para fallas de verdad. Aquí sin
            # condicionar a `propio`: el turno propio se abre a mano justamente para que
            # este sea el único sitio que decide, y no salgan dos errores por un fallo.
            if not rechazo:
                capturar_error(exc, {"accion": nombre, "usuario": usuario})

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper_async(*args, **kwargs):
                if not activa():
                    return await fn(*args, **kwargs)
                entrada, usuario = _contexto(kwargs)
                with _turno_o_actual(canal="panel") as propio:
                    try:
                        resultado = await fn(*args, **kwargs)
                    except Exception as exc:
                        _fallo(exc, entrada, usuario, propio)
                        raise
                    _registrar(entrada, usuario, resultado, propio)
                    return resultado
            return wrapper_async

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not activa():
                return fn(*args, **kwargs)
            entrada, usuario = _contexto(kwargs)
            with _turno_o_actual(canal="panel") as propio:
                try:
                    resultado = fn(*args, **kwargs)
                except Exception as exc:
                    _fallo(exc, entrada, usuario, propio)
                    raise
                _registrar(entrada, usuario, resultado, propio)
                return resultado
        return wrapper
    return deco
