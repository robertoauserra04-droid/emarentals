"""Reporte de errores al tablero de la flota de Vella (capa VER).

Es la mitad "logs" de la observabilidad: la caja negra guarda el turno COMPLETO en el
repo del cliente (y se vacía a las 24 h), y esto manda el error al panel para que salga
en el tablero 🐞 sin que nadie tenga que ir a buscarlo.

Best-effort de verdad: si el panel está caído, si falta la llave o si no hay red, la
llamada se traga el error. Reportar un problema nunca puede causar otro.

CERO EDICIONES. Se copia tal cual a la raíz del repo (o junto al resto de la caja negra
si el repo es de paquete). Toda la configuración es por env:

    FLEET_INGEST_KEY   la MISMA llave que el panel (sin ella no se reporta nada)
    VELLA_CLIENTE      el slug del cliente, igual al de la pestaña 🛡 del panel
    VELLA_PANEL_URL    opcional; default el panel de producción

OJO con `VELLA_CLIENTE`: antes cada repo traía su slug hardcodeado
(`_CLIENTE_DEFAULT = "dorian"`), que es justo lo que hacía que este archivo divergiera
entre repos y dejara de ser copiable. Si falta la env, el error se reporta igual pero
etiquetado como "sin-slug", que es visible en el tablero y se corrige poniendo la env —
mucho mejor que atribuirle el error al cliente equivocado.
"""
import os

_PANEL_URL = os.getenv("VELLA_PANEL_URL", "https://vellapanel.up.railway.app")


def _cliente() -> str:
    return (os.getenv("VELLA_CLIENTE") or os.getenv("CAJA_NEGRA_CLIENTE") or "sin-slug").strip()


def reportar_error_vella(mensaje, detalle="", origen="bot", nivel="error", phone=""):
    try:
        if not os.getenv("FLEET_INGEST_KEY"):
            return          # sin llave el panel rechazaría el POST; no gastes la llamada
        payload = {
            "cliente": _cliente(),
            "mensaje": str(mensaje)[:300],
            "detalle": str(detalle)[:8000],
            "origen": origen, "nivel": nivel, "phone": phone,
        }
        headers = {"X-Fleet-Key": os.getenv("FLEET_INGEST_KEY", "")}
        url = _PANEL_URL.rstrip("/") + "/api/fleet/errores"
        try:
            import httpx
            httpx.post(url, json=payload, headers=headers, timeout=5)
        except ImportError:
            import requests
            requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception:
        pass  # nunca tirar el bot por reportar
