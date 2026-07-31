"""Cliente WhatsApp (Kapso) — envío de texto y de plantillas. Reuso de vella panel.

En dev (sin KAPSO_API_KEY) todo es no-op logueado, para poder probar el flujo sin mandar
mensajes reales.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

META_BASE = "https://api.kapso.ai/meta/whatsapp/v24.0"


class KapsoSendError(RuntimeError):
    """Falla de envío de Kapso/Meta con el motivo REAL a la vista (port de bienesraicesEnrique).

    `resp.raise_for_status()` solo dejaba "400 Bad Request": el código y mensaje de Meta
    (p.ej. 131047 = fuera de la ventana de 24h, 131026 = número no entregable) viven en el
    cuerpo de la respuesta y se perdían. Aquí se conservan en `.detail`.

    `permanente` = True para 4xx (reintentar NO ayuda); False para 5xx (transitorio).
    """

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = (detail or "").strip()
        self.permanente = 400 <= status_code < 500
        super().__init__(f"Kapso {status_code}: {self.detail[:500]}")


def _check(resp) -> None:
    """Reemplaza a resp.raise_for_status(): conserva el motivo de Meta en la excepción."""
    if resp.status_code >= 400:
        logger.error("Kapso send falló %s: %s", resp.status_code, resp.text)
        raise KapsoSendError(resp.status_code, resp.text)


def _headers() -> dict:
    return {"X-API-Key": settings.kapso_api_key, "Content-Type": "application/json"}


def _enabled() -> bool:
    return bool(settings.kapso_api_key and settings.kapso_phone_number_id)


def send_text_sync(phone: str, body: str) -> str | None:
    """Envía un texto libre (dentro de la ventana de 24h). Devuelve el wamid o None."""
    if not _enabled():
        logger.warning("[kapso:dev] TEXT → %s: %s", phone, body)
        return None
    payload = {"messaging_product": "whatsapp", "to": phone, "type": "text",
               "text": {"body": body}}
    with httpx.Client() as client:
        resp = client.post(f"{META_BASE}/{settings.kapso_phone_number_id}/messages",
                           headers=_headers(), json=payload, timeout=15)
        _check(resp)
        data = resp.json()
    try:
        return data["messages"][0]["id"]
    except Exception:  # noqa: BLE001
        return None


def send_template_sync(phone: str, template_name: str, lang: str = "es_MX") -> str | None:
    """Envía una plantilla aprobada (obligatorio fuera de la ventana de 24h). 0 variables."""
    if not _enabled():
        logger.warning("[kapso:dev] TEMPLATE %s → %s", template_name, phone)
        return None
    payload = {
        "messaging_product": "whatsapp", "to": phone, "type": "template",
        "template": {"name": template_name, "language": {"code": lang}},
    }
    return _post(payload)


def send_template_vars_sync(phone: str, template_name: str, variables: list[str],
                            lang: str = "es_MX") -> str | None:
    """Plantilla con variables posicionales en el body ({{1}}, {{2}}, ...)."""
    if not _enabled():
        logger.warning("[kapso:dev] TEMPLATE %s(%s) → %s", template_name, variables, phone)
        return None
    componentes = []
    if variables:
        componentes = [{"type": "body",
                        "parameters": [{"type": "text", "text": str(v)} for v in variables]}]
    payload = {
        "messaging_product": "whatsapp", "to": phone, "type": "template",
        "template": {"name": template_name, "language": {"code": lang},
                     "components": componentes},
    }
    return _post(payload)


def send_media_sync(phone: str, tipo: str, url: str, caption: str | None = None,
                    filename: str | None = None) -> str | None:
    """Envía media por URL. tipo: 'image' | 'video' | 'document'."""
    if not _enabled():
        logger.warning("[kapso:dev] %s → %s: %s", tipo.upper(), phone, url)
        return None
    media: dict = {"link": url}
    if caption and tipo in ("image", "video", "document"):
        media["caption"] = caption
    if filename and tipo == "document":
        media["filename"] = filename
    payload = {"messaging_product": "whatsapp", "to": phone, "type": tipo, tipo: media}
    return _post(payload)


def _post(payload: dict) -> str | None:
    with httpx.Client() as client:
        resp = client.post(f"{META_BASE}/{settings.kapso_phone_number_id}/messages",
                           headers=_headers(), json=payload, timeout=15)
        _check(resp)
        data = resp.json()
    try:
        return data["messages"][0]["id"]
    except Exception:  # noqa: BLE001
        return None
