"""Middleware ASGI de la caja negra — el PISO de cobertura del panel.

Abre un `turno` por cada request que ESCRIBE (POST/PUT/PATCH/DELETE) y registra un
evento `accion_panel` con el cuerpo de la solicitud, el status, cuánto tardó, quién lo
hizo y —si falló— el detalle de POR QUÉ se negó. Con esto ninguna escritura del panel
queda sin registrar sin tocar los ~1000 handlers de la flota uno por uno.

Es ASGI **puro** a propósito, y esto NO se puede "limpiar" a `BaseHTTPMiddleware`:
ese corre el endpoint en otra task y el `contextvar` del turno no propagaría, así que
cada evento del decorador se desprendería de su request y `registrar()` empezaría a
escribir `turno_id="sin-turno"` — sin ningún error, solo datos inútiles. El test
`test_reusa_turno_ambiente` existe para que esa regresión truene fuerte.

Se monta como el ÚLTIMO `add_middleware` para quedar el más externo: así ve el status
final y atrapa las excepciones que escapan.

    from caja_negra_mw import CajaNegraMiddleware      # o app./core.
    app.add_middleware(CajaNegraMiddleware)

Qué NO registra:
  • lecturas (GET): son el grueso del tráfico y no cambian nada;
  • las rutas de `_SALTAR`: los webhooks del bot abren su propio turno, y leer la caja
    (`/api/fleet`, `/api/robustez`) no debe escribir en la caja.

SECRETOS — dos capas, porque una lista de prefijos no alcanza (en la flota hay logins
en /login, /api/login, /api/auth/login y /api/empleada/login):
  1. si la RUTA huele a secreto no se captura el body NI el cuerpo del error —lo
     segundo importa porque un 422 de FastAPI hace eco del input;
  2. todo body JSON que sí se captura se camina y las CLAVES sensibles se tapan.
Falla cerrado: ante la duda, se tapa.

La caja negra jamás debe tumbar producción: si el propio logging falla, se ignora.
"""

from __future__ import annotations

import json
import time

from observabilidad import (CLIENTE, archivar_caso, capturar_error, iniciar_turno,
                            registrar, resolver_usuario, set_usuario)

_MUTAN = {"POST", "PUT", "PATCH", "DELETE"}

# Unión de toda la flota, para que este archivo sea idéntico en los 18 repos.
_SALTAR = ("/whatsapp", "/webhook", "/webhooks", "/kapso", "/sinch", "/stripe",
           "/api/fleet", "/api/robustez", "/health", "/static", "/media",
           "/favicon", "/docs", "/openapi", "/redoc")

_MAX_BODY = 4096       # bytes de solicitud que se guardan por evento
_MAX_ERROR = 2048      # bytes de respuesta de error que se guardan

# Rutas donde el body entero es secreto → no se captura nada.
_RUTA_SENSIBLE = ("login", "logout", "password", "contrasena", "contraseña",
                  "token", "/mfa", "reset")
# Claves que se tapan dentro de cualquier body que sí se capture.
_CLAVE_SENSIBLE = ("password", "contrasena", "contraseña", "pass", "secret",
                   "token", "apikey", "api_key", "authorization", "pin", "cvv",
                   "tarjeta", "card")


def _texto(b: bytes, limite: int) -> str:
    t = b[:limite].decode("utf-8", errors="replace")
    return t + ("…" if len(b) > limite else "")


def _clave_sensible(nombre) -> bool:
    n = str(nombre).lower()
    return any(f in n for f in _CLAVE_SENSIBLE)


def _redactar(valor):
    """Camina el JSON tapando claves sensibles. Conserva la estructura."""
    if isinstance(valor, dict):
        return {k: ("***" if _clave_sensible(k) else _redactar(v))
                for k, v in valor.items()}
    if isinstance(valor, list):
        return [_redactar(v) for v in valor]
    return valor


def _saltar(ruta: str) -> bool:
    r = ruta.lower()
    return any(r.startswith(p) for p in _SALTAR) or "webhook" in r


class CajaNegraMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        metodo = scope.get("method", "")
        ruta = scope.get("path", "")
        if metodo not in _MUTAN or _saltar(ruta):
            await self.app(scope, receive, send)
            return

        autenticado = any(k == b"authorization" for k, _ in scope.get("headers", []))
        ruta_baja = ruta.lower()
        seguro = not any(p in ruta_baja for p in _RUTA_SENSIBLE)

        # El turno se abre en ESTA task → el contextvar propaga al endpoint (sync, en
        # threadpool: anyio copia el contexto) y a lo que llame por dentro, así que un
        # cobro registra su `metrica` en el mismo turno.
        tid = iniciar_turno(canal="panel", cliente=CLIENTE)

        cuerpo = bytearray()

        async def receive_wrapper():
            m = await receive()
            if seguro and m.get("type") == "http.request" and len(cuerpo) < _MAX_BODY:
                cuerpo.extend(m.get("body", b""))
            return m

        estado = {"code": None, "exc": False}
        error_body = bytearray()

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                estado["code"] = message.get("status")
            elif (message.get("type") == "http.response.body" and seguro
                  and (estado["code"] or 0) >= 400 and len(error_body) < _MAX_ERROR):
                error_body.extend(message.get("body", b""))
            await send(message)

        t0 = time.monotonic()
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:            # excepción no controlada → error + caso
            estado["exc"] = True
            try:
                capturar_error(exc, {"ruta": ruta, "metodo": metodo})
                if tid:
                    archivar_caso(tid, reportado=f"500 en {metodo} {ruta}")
            except Exception:               # noqa: BLE001
                pass
            raise
        finally:
            try:
                # El usuario se resuelve AQUÍ, después de que corrió la app: así funciona
                # también con cookie de sesión (SessionMiddleware llena el mismo `scope`)
                # sin depender del orden de los middlewares.
                usuario = None
                try:
                    usuario = resolver_usuario(scope)
                    set_usuario(usuario)
                except Exception:           # noqa: BLE001
                    pass

                # Si la excepción escapó, el status nunca se envió: es un 500. Sin esto
                # `code` quedaba en 0 y TODO 500 real se registraba como `info`, vaciando
                # el contador de errores de resumen() y de /turnos.
                code = estado["code"] or (500 if estado["exc"] else 0)

                datos = {
                    "accion": f"{metodo} {ruta}",   # nunca nulo: un consumidor, una clave
                    "usuario": usuario,
                    "params": None,
                    "antes": None,                  # el diff de la fila lo pone el
                    "despues": None,                # decorador en las rutas de dinero
                    "metodo": metodo,
                    "ruta": ruta,                   # lo usa /probar-datos-reales por --texto
                    "status": code or None,
                    "duracion_ms": int((time.monotonic() - t0) * 1000),
                    "autenticado": autenticado,
                    "origen": "middleware",
                }
                if seguro and cuerpo:
                    crudo = bytes(cuerpo)
                    try:
                        datos["params"] = _redactar(json.loads(crudo))
                    except Exception:       # noqa: BLE001
                        # No es JSON: no se puede redactar por clave, así que si huele a
                        # secreto se descarta entero (falla cerrado).
                        texto = _texto(crudo, _MAX_BODY)
                        datos["params"] = ("(omitido: posible secreto)"
                                           if _clave_sensible(texto) else texto)
                if error_body:
                    datos["por_que_fallo"] = _texto(bytes(error_body), _MAX_ERROR)

                registrar("accion_panel", datos,
                          nivel="error" if code >= 500 else
                                "warn" if code >= 400 else "info")
            except Exception:               # noqa: BLE001
                pass
