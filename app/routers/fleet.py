"""Extracción de la caja negra SOLO para Vella (el visor de vellapanel).

Se copia a `routes/fleet.py` (repos planos), `app/routers/fleet.py` (repos de paquete)
o `core/fleet_caja.py` (colegios, que no tienen carpeta de routers). CERO ediciones.

Contrato: lo consume `vellapanel/app/routers/fleet.py`, que proxea server-side a
`{url_del_repo}/api/fleet/caja-negra` + /turnos, /turno/{id}, /casos, /casos/{archivo},
/db, /resumen, mandando `X-Fleet-Key`. La llave nunca llega al navegador.

Protegido por header X-Fleet-Key contra la env FLEET_INGEST_KEY (la misma que usa
vella_fleet.py para reportar errores al panel). Sin esa env, TODO responde 404: apagado
por default. Llave incorrecta también 404, para no revelar que la ruta existe — y el
panel ya interpreta ese 404 como "este bot todavía no expone la caja negra". El JWT del
panel del cliente NO da acceso aquí, y el frontend del cliente no referencia estas rutas.

OJO: aquí es 404, pero en `/api/robustez/cajanegra` es 401. No es descuido: son dos
consumidores con contratos distintos. El agente de robustez trata el 404 como "falta el
drop-in" y necesita distinguirlo de una llave mal configurada.
"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse

import observabilidad as caja

# include_in_schema=False: no aparecen en /openapi.json ni en /docs del cliente.
router = APIRouter(prefix="/api/fleet/caja-negra", tags=["fleet"], include_in_schema=False)


def _verificar(x_fleet_key: str) -> None:
    key = os.getenv("FLEET_INGEST_KEY", "")
    if not key or not hmac.compare_digest(x_fleet_key, key):
        raise HTTPException(404)


def _core():
    """El módulo core, o 404 si el repo no tiene caja negra instalada."""
    if not caja.activa() or caja._cn is None:
        raise HTTPException(404, "Este cliente no tiene caja negra activa.")
    return caja._cn


def _db_path() -> str:
    """La ruta real que usa el core AHORA, no la que había en el import.

    Se lee del core en vez de releer el env aquí para que no puedan discrepar, y para
    que la fixture de tests (que parchea `_DB_PATH`) también aplique a estas rutas.
    """
    return getattr(_core(), "_DB_PATH", os.getenv("CAJA_NEGRA_DB", "caja_negra.db"))


def _casos_dir() -> str:
    return getattr(_core(), "_CASOS_DIR", os.getenv("CAJA_NEGRA_CASOS_DIR", "casos"))


@router.get("/turnos")
def listar_turnos(horas: int = 24, x_fleet_key: str = Header(default="")):
    """Resumen de turnos recientes: inicio, canal, eventos, errores y primer mensaje."""
    _verificar(x_fleet_key)
    cn = _core()
    horas = max(1, min(horas, 24 * 7))
    desde_ms = int((datetime.now(timezone.utc) - timedelta(hours=horas)).timestamp() * 1000)
    eventos = cn.buscar(desde=desde_ms, limite=5000)
    turnos: dict[str, dict] = {}
    for e in eventos:
        t = turnos.setdefault(e["turno_id"], {
            "turno_id": e["turno_id"], "inicio": e["ts"], "canal": e["canal"],
            "eventos": 0, "errores": 0, "avisos": 0, "primer_mensaje": None,
        })
        t["eventos"] += 1
        if e["nivel"] == "error":
            t["errores"] += 1
        elif e["nivel"] == "warn":
            t["avisos"] += 1
        datos = e.get("datos")
        if not isinstance(datos, dict):
            continue
        if t["primer_mensaje"] is None:
            # Un turno del bot arranca con `mensaje_entrante`; uno del panel no tiene
            # mensaje, así que se etiqueta con la acción para que la tabla no salga vacía.
            if e["evento"] == "mensaje_entrante":
                t["primer_mensaje"] = (datos.get("texto") or "")[:160]
            elif e["evento"] in ("accion_panel", "accion_ejecutada"):
                t["primer_mensaje"] = (datos.get("accion") or "")[:160]
    return {"horas": horas, "n_turnos": len(turnos),
            "turnos": sorted(turnos.values(), key=lambda t: t["inicio"], reverse=True)}


@router.get("/turno/{turno_id}")
def detalle_turno(turno_id: str, x_fleet_key: str = Header(default="")):
    """Todos los eventos de un turno, en orden cronológico. Esto es el detalle fino."""
    _verificar(x_fleet_key)
    eventos = _core().buscar(turno_id=turno_id, limite=1000)
    if not eventos:
        raise HTTPException(404, "Turno no encontrado (o ya salió de la ventana de retención).")
    return {"turno_id": turno_id, "n_eventos": len(eventos), "eventos": eventos}


@router.get("/resumen")
def resumen(horas: int = 24, x_fleet_key: str = Header(default="")):
    """Errores reales, latencia máxima y total de eventos. Igual que el de robustez."""
    _verificar(x_fleet_key)
    cn = _core()
    if not hasattr(cn, "resumen"):
        raise HTTPException(404, "Core sin resumen(): actualiza caja_negra.py.")
    return cn.resumen(max(1, min(horas, 24 * 7)))


@router.get("/db")
def descargar_db(x_fleet_key: str = Header(default="")):
    """Descarga el SQLite completo. Es la vía para salvar evidencia antes de un redeploy."""
    _verificar(x_fleet_key)
    ruta = _db_path()
    if not os.path.exists(ruta):
        raise HTTPException(404, "Aún no hay caja negra grabada en este deploy.")
    return FileResponse(ruta, filename="caja_negra.db",
                        media_type="application/octet-stream")


@router.get("/casos")
def listar_casos(x_fleet_key: str = Header(default="")):
    """Lista los casos congelados (turnos archivados como evidencia/fixture)."""
    _verificar(x_fleet_key)
    d = Path(_casos_dir())
    casos = sorted(d.glob("*.json"), reverse=True) if d.is_dir() else []
    return {"n_casos": len(casos),
            "casos": [{"archivo": c.name, "bytes": c.stat().st_size} for c in casos]}


@router.get("/casos/{archivo}")
def descargar_caso(archivo: str, x_fleet_key: str = Header(default="")):
    """Descarga un caso congelado por nombre de archivo."""
    _verificar(x_fleet_key)
    nombre = Path(archivo).name          # sin rutas: bloquea path traversal
    ruta = Path(_casos_dir()) / nombre
    if not nombre.endswith(".json") or not ruta.is_file():
        raise HTTPException(404, "Caso no encontrado.")
    return FileResponse(ruta, filename=nombre, media_type="application/json")
