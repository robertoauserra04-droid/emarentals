"""
caja_negra.py — Caja negra de Vella (observabilidad + reproducibilidad).

Un solo evento JSON por cada acción del bot/panel. Todos los eventos de un mismo
turno de conversación comparten un `turno_id`. Se guardan en una base SQLite APARTE
(no la de negocio), con retención rodante de 24 h. Cuando algo falla o se cobra, un
turno completo se "archiva" como CASO congelado: ese caso es la evidencia y, después,
el fixture que el skill /caso-produccion reproduce hasta la UI.

Diseño (por qué así):
  • DB separada `caja_negra.db` → no bloquea ni ensucia la DB de negocio; es efímera
    (24 h) y se puede descargar entera para inspeccionar "en qué minuto pasó qué".
  • Retención rodante 24 h → el almacenamiento queda ACOTADO al tráfico de un día
    (kilobytes–pocos MB en los bots de Vella). Barato y sostenible.
  • Cero dependencias externas (solo stdlib) → funciona igual en cualquier repo Python
    sea plano o refactor, SQLite o Postgres de negocio.
  • contextvars → una vez abierto el turno, `registrar()` no necesita pasar el id por
    cada función: la instrumentación queda mínima y no invasiva (sirve en async).

Uso mínimo (ver INTEGRACION.md para los 6 puntos de enganche):

    from caja_negra import turno, registrar, archivar_caso

    with turno(cliente="aurasalon", canal="whatsapp", telefono=numero):
        registrar("mensaje_entrante", {"texto": texto, "payload": payload_crudo})
        registrar("prompt_ia",        {"system": system, "messages": messages, "modelo": modelo})
        registrar("respuesta_ia",     {"texto": respuesta_cruda})
        registrar("comando_detectado",{"comando": "CITA_CONFIRMADA", "args": args})
        registrar("accion_ejecutada", {"accion": "crear_cita", "antes": a, "despues": b})
        registrar("mensaje_saliente", {"texto": salida})
    # si algo lanza excepción dentro del `with`, se registra evento `error` con traceback.

En los repos NO se importa este módulo directamente: se usa la fachada
`observabilidad.py`, que lo resuelve viva donde viva (raíz, `app/`, `core/`), lo
vuelve no-op si falta y aporta el decorador `@accion_panel`. Ver PROMPT-INSTRUMENTAR.md.

OJO AL PROBAR: la configuración se lee EN IMPORT (constantes de módulo, más abajo).
Por eso `monkeypatch.setenv("CAJA_NEGRA_DB", ...)` NO basta en un test: hay que
parchear también el atributo, o los tests escriben en la caja real del repo.

    monkeypatch.setenv("CAJA_NEGRA_DB", str(tmp_path / "caja.db"))   # para quien lea el env
    monkeypatch.setattr(cn, "_DB_PATH", str(tmp_path / "caja.db"))   # para este módulo

Se mantiene así a propósito: pasarlo a call-time rompería en silencio las suites que
ya hacen este doble parche.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import sys
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

# ── Configuración (todo por env, con defaults sanos) ─────────────────────────────
_DB_PATH        = os.getenv("CAJA_NEGRA_DB", "caja_negra.db")
_RETENCION_H    = int(os.getenv("CAJA_NEGRA_RETENCION_HORAS", "24"))
_CASOS_DIR      = os.getenv("CAJA_NEGRA_CASOS_DIR", "casos")
_CLIENTE_DEF    = os.getenv("CAJA_NEGRA_CLIENTE", os.getenv("SALON_NOMBRE", "desconocido"))
_MAX_DATOS      = int(os.getenv("CAJA_NEGRA_MAX_DATOS_BYTES", "65536"))  # 64 KB por evento
_ACTIVA         = os.getenv("CAJA_NEGRA_ACTIVA", "1") != "0"

# Vocabulario de eventos (la línea del tiempo de un turno). No es rígido: si mandas
# uno fuera de esta lista se guarda igual, pero úsalos para que el skill los entienda.
EVENTOS = (
    "mensaje_entrante",    # input crudo (webhook/panel) — el punto de partida exacto
    "contexto_cargado",    # historial + estado de DB que se le pasó a la IA
    "prompt_ia",           # system prompt + messages EXACTOS enviados al modelo
    "respuesta_ia",        # salida cruda del modelo (texto con comandos embebidos)
    "comando_detectado",   # qué comando parseó el regex (CITA_CONFIRMADA|..., COBRO|...)
    "accion_ejecutada",    # resultado de ejecutar la acción, con antes/después
    "mensaje_saliente",    # lo que se le mandó de vuelta al cliente
    "accion_panel",        # acción del panel web (click/endpoint): cobrar, cerrar caja, etc.
    "metrica",             # dinero/tokens/costo: {"monto": ..., "tokens": ...}
    "error",               # excepción con traceback + snapshot del turno
)

# ── Estado del turno actual (por request/tarea async) ────────────────────────────
_turno_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("caja_negra_turno", default=None)
_prune_counter = 0


def _ahora():
    return datetime.now(timezone.utc)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute(
        """CREATE TABLE IF NOT EXISTS eventos(
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            epoch_ms INTEGER NOT NULL,
            ts       TEXT    NOT NULL,   -- ISO-8601 UTC
            cliente  TEXT    NOT NULL,
            canal    TEXT,
            turno_id TEXT    NOT NULL,
            sesion_id TEXT,
            evento   TEXT    NOT NULL,
            nivel    TEXT    NOT NULL,
            lat_ms   INTEGER,
            datos    TEXT               -- JSON
        )"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS ix_eventos_epoch ON eventos(epoch_ms)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_eventos_turno ON eventos(turno_id)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_eventos_busq  ON eventos(cliente, evento, epoch_ms)")
    return c


def _prune(c: sqlite3.Connection):
    """Retención rodante: borra lo que pasó de 24 h. Deja el almacenamiento acotado."""
    corte = int((_ahora().timestamp() - _RETENCION_H * 3600) * 1000)
    c.execute("DELETE FROM eventos WHERE epoch_ms < ?", (corte,))


def _sesion_id(telefono: str | None) -> str | None:
    """Id estable por contacto (para agrupar), sin exponer el número en los índices."""
    if not telefono:
        return None
    import hashlib
    return hashlib.sha256(str(telefono).encode()).hexdigest()[:12]


def _serializar(datos) -> str:
    try:
        s = json.dumps(datos, ensure_ascii=False, default=str)
    except Exception:
        s = json.dumps({"_no_serializable": str(datos)}, ensure_ascii=False)
    if len(s) > _MAX_DATOS:                       # guarda anti-bloat: nada de payloads gigantes
        s = s[:_MAX_DATOS] + '…"__truncado":true}'
    return s


# ── API pública ──────────────────────────────────────────────────────────────────
def iniciar_turno(cliente=None, canal=None, telefono=None, turno_id=None) -> str:
    """Abre un turno y lo fija en el contexto. Devuelve el turno_id."""
    tid = turno_id or uuid.uuid4().hex[:16]
    _turno_ctx.set({
        "turno_id": tid,
        "cliente": cliente or _CLIENTE_DEF,
        "canal": canal,
        "sesion_id": _sesion_id(telefono),
        "telefono": telefono,
        "ultimo_ms": _ahora().timestamp() * 1000,
    })
    return tid


NIVELES = ("info", "warn", "error")


def _nivel(nivel) -> str:
    """Normaliza el nivel al vocabulario de FORMATO.md (info|warn|error).

    Existe porque ya pasó: un middleware escribía `"warning"`, que no coincide con
    `buscar(nivel="warn")` ni con el contador de errores de `resumen()`, así que los
    4xx quedaban invisibles sin que nada fallara.
    """
    n = str(nivel or "info").lower()
    if n in NIVELES:
        return n
    if n.startswith("warn") or n in ("aviso", "advertencia"):
        return "warn"
    if n.startswith("err") or n in ("critical", "fatal", "crit"):
        return "error"
    return "info"


def registrar(evento: str, datos=None, nivel: str = "info") -> None:
    """Escribe un evento del turno actual. No revienta nunca la app si falla."""
    if not _ACTIVA:
        return
    nivel = _nivel(nivel)
    st = _turno_ctx.get()
    if st is None:                                # sin turno abierto → turno huérfano
        st = {"turno_id": "sin-turno", "cliente": _CLIENTE_DEF, "canal": None,
              "sesion_id": None, "ultimo_ms": _ahora().timestamp() * 1000}
        _turno_ctx.set(st)
    try:
        now = _ahora()
        now_ms = now.timestamp() * 1000
        lat = int(now_ms - st.get("ultimo_ms", now_ms))
        st["ultimo_ms"] = now_ms
        global _prune_counter
        with _conn() as c:
            c.execute(
                "INSERT INTO eventos(epoch_ms,ts,cliente,canal,turno_id,sesion_id,evento,nivel,lat_ms,datos)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (int(now_ms), now.isoformat(), st["cliente"], st.get("canal"),
                 st["turno_id"], st.get("sesion_id"), evento, nivel, lat, _serializar(datos)),
            )
            _prune_counter += 1
            if _prune_counter % 200 == 0:         # poda amortizada: no en cada escritura
                _prune(c)
    except Exception:                             # la caja negra JAMÁS tumba producción
        pass


def capturar_error(exc: BaseException | None = None, datos=None) -> None:
    """Registra un evento `error` con traceback + los datos que le pases."""
    tb = "".join(traceback.format_exception(exc)) if exc else traceback.format_exc()
    registrar("error", {"tipo": type(exc).__name__ if exc else "error", "traceback": tb,
                        **(datos or {})}, nivel="error")


@contextmanager
def turno(cliente=None, canal=None, telefono=None, turno_id=None):
    """Context manager: abre el turno y captura cualquier excepción como evento `error`."""
    token = _turno_ctx.set(None)
    iniciar_turno(cliente, canal, telefono, turno_id)
    try:
        yield _turno_ctx.get()["turno_id"]
    except Exception as e:
        capturar_error(e)
        raise
    finally:
        _turno_ctx.reset(token)


# ── Búsqueda ("en qué minuto pasó qué") ──────────────────────────────────────────
def buscar(desde=None, hasta=None, cliente=None, evento=None, nivel=None,
           turno_id=None, sesion=None, texto=None, limite=500) -> list[dict]:
    """
    Consulta la caja negra. Fechas en ISO ('2026-07-16T19:50') o epoch_ms.
    `texto` busca subcadena dentro de `datos`. Devuelve dicts ordenados por tiempo.
    """
    def _ms(v):
        if v is None or isinstance(v, int):
            return v
        return int(datetime.fromisoformat(v).replace(tzinfo=timezone.utc).timestamp() * 1000)

    q = "SELECT epoch_ms,ts,cliente,canal,turno_id,sesion_id,evento,nivel,lat_ms,datos FROM eventos WHERE 1=1"
    p: list = []
    for col, val in (("epoch_ms>=?", _ms(desde)), ("epoch_ms<=?", _ms(hasta)),
                     ("cliente=?", cliente), ("evento=?", evento), ("nivel=?", nivel),
                     ("turno_id=?", turno_id), ("sesion_id=?", sesion)):
        if val is not None:
            q += f" AND {col}"; p.append(val)
    if texto:
        q += " AND datos LIKE ?"; p.append(f"%{texto}%")
    q += " ORDER BY epoch_ms ASC LIMIT ?"; p.append(limite)
    with _conn() as c:
        cols = ["epoch_ms", "ts", "cliente", "canal", "turno_id", "sesion_id", "evento", "nivel", "lat_ms", "datos"]
        out = []
        for row in c.execute(q, p).fetchall():
            d = dict(zip(cols, row))
            try:
                d["datos"] = json.loads(d["datos"]) if d["datos"] else None
            except Exception:
                pass
            out.append(d)
        return out


# ── Archivar un CASO (congelar un turno como evidencia/fixture) ───────────────────
def archivar_caso(turno_id: str, reportado: str = "", esperado: str = "",
                  estado_db: dict | None = None, destino: str | None = None) -> str:
    """
    Congela TODOS los eventos de un turno en casos/<fecha>-<cliente>-<turno>.json.
    Ese archivo es la evidencia y el fixture que /caso-produccion reproduce.
    """
    eventos = buscar(turno_id=turno_id, limite=10000)
    if not eventos:
        raise ValueError(f"No hay eventos para el turno {turno_id} (¿ya expiró la retención de {_RETENCION_H}h?)")
    cliente = eventos[0]["cliente"]
    fecha = eventos[0]["ts"][:10]
    caso = {
        "id": f"{fecha}-{cliente}-{turno_id}",
        "cliente": cliente,
        "canal": eventos[0].get("canal"),
        "reportado": reportado,           # qué se vio mal (palabras del cliente/soporte)
        "esperado": esperado,             # qué debió pasar
        "estado": "abierto",              # abierto → reproducido → arreglado → blindado
        "capturado_ts": _ahora().isoformat(),
        "estado_db_relevante": estado_db or {},   # seed mínimo para reproducir
        "eventos": eventos,               # la línea del tiempo COMPLETA del turno
    }
    os.makedirs(destino or _CASOS_DIR, exist_ok=True)
    ruta = os.path.join(destino or _CASOS_DIR, f"{caso['id']}.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(caso, f, ensure_ascii=False, indent=2)
    return ruta


# ── Resumen para el agente de robustez (endpoint /api/robustez/cajanegra) ─────────
def resumen(horas: int = 24) -> dict:
    """Resumen de las últimas `horas` para el carril caja negra del agente de robustez:
    errores REALES de producción, latencia máxima observada y total de eventos."""
    desde_ms = int((_ahora().timestamp() - horas * 3600) * 1000)
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM eventos WHERE epoch_ms >= ?", (desde_ms,)).fetchone()[0]
        n_err = c.execute("SELECT COUNT(*) FROM eventos WHERE epoch_ms >= ? AND nivel='error'",
                          (desde_ms,)).fetchone()[0]
        err_rows = c.execute(
            "SELECT turno_id, ts, datos FROM eventos WHERE epoch_ms >= ? AND nivel='error'"
            " ORDER BY epoch_ms DESC LIMIT 20", (desde_ms,)).fetchall()
        # Latencia máxima SIN traer el `datos` de toda la ventana: con cobertura de
        # middleware el volumen sube ~10x y horas=168 traería megabytes.
        lat_max = c.execute("SELECT MAX(lat_ms) FROM eventos WHERE epoch_ms >= ?",
                            (desde_ms,)).fetchone()[0] or 0
        # `duracion_ms` (lo que de verdad tardó un request) solo lo escribe el
        # middleware en accion_panel. Se saca con JSON1 si está; si no, escaneo acotado.
        try:
            dur = c.execute(
                "SELECT MAX(CAST(json_extract(datos,'$.duracion_ms') AS INTEGER)) FROM eventos"
                " WHERE epoch_ms >= ? AND evento='accion_panel'", (desde_ms,)).fetchone()[0] or 0
        except sqlite3.Error:
            dur = 0
            for (datos,) in c.execute(
                    "SELECT datos FROM eventos WHERE epoch_ms >= ? AND evento='accion_panel'"
                    " ORDER BY epoch_ms DESC LIMIT 2000", (desde_ms,)).fetchall():
                try:
                    cand = (json.loads(datos) if datos else {}).get("duracion_ms") or 0
                except Exception:
                    cand = 0
                if isinstance(cand, (int, float)) and cand > dur:
                    dur = int(cand)
    lat_max = int(max(lat_max or 0, dur or 0))
    errores = []
    for tid, ts, datos in err_rows:
        try:
            d = json.loads(datos) if datos else {}
        except Exception:
            d = {}
        errores.append({"turno_id": tid, "ts": ts, "tipo": d.get("tipo") or "error",
                        "traceback": (d.get("traceback") or d.get("detalle") or "")[:6000]})
    return {"instrumentada": True, "eventos_total": total, "n_errores": n_err,
            "lat_max_ms": lat_max, "errores": errores}


# ── CLI: python -m caja_negra ... ────────────────────────────────────────────────
def _cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="caja_negra", description="Caja negra de Vella")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar", help="Buscar eventos (en qué minuto pasó qué)")
    for flag in ("--desde", "--hasta", "--cliente", "--evento", "--nivel", "--turno-id", "--sesion", "--texto"):
        b.add_argument(flag)
    b.add_argument("--limite", type=int, default=100)
    a = sub.add_parser("archivar", help="Archivar un turno como caso")
    a.add_argument("turno_id")
    a.add_argument("--reportado", default="")
    a.add_argument("--esperado", default="")
    ns = ap.parse_args(argv)
    if ns.cmd == "buscar":
        filas = buscar(desde=ns.desde, hasta=ns.hasta, cliente=ns.cliente, evento=ns.evento,
                       nivel=ns.nivel, turno_id=ns.turno_id, sesion=ns.sesion, texto=ns.texto, limite=ns.limite)
        for f in filas:
            print(f"{f['ts']}  {f['turno_id']}  {f['evento']:<18} {f['nivel']:<5} {f['lat_ms']}ms  "
                  f"{json.dumps(f['datos'], ensure_ascii=False)[:160]}")
        print(f"\n{len(filas)} evento(s).")
    elif ns.cmd == "archivar":
        print("Caso archivado en:", archivar_caso(ns.turno_id, ns.reportado, ns.esperado))


if __name__ == "__main__":
    _cli(sys.argv[1:])
