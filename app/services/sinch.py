"""Sinch Conversation API — envío + verificación de firma para Instagram y Messenger.

WhatsApp NO pasa por aquí (sigue por Kapso). Este módulo solo se usa cuando el canal de la
conversación es 'instagram' o 'messenger'. Sin credenciales, no envía (dev). Port de vella panel.

Auth: OAuth2 client_credentials (Access Key de Sinch) → bearer token cacheado en memoria.
"""
import base64
import hashlib
import hmac
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger("sinch")

_AUTH_URL = "https://auth.sinch.com/oauth2/token"
_token_cache: dict = {}

_SINCH_CHANNEL = {"instagram": "INSTAGRAM", "messenger": "MESSENGER", "whatsapp": "WHATSAPP"}


def _base_url() -> str:
    region = (settings.sinch_region or "us").lower()
    return f"https://{region}.conversation.api.sinch.com/v1/projects/{settings.sinch_project_id}"


def _get_token() -> str | None:
    if not (settings.sinch_key_id and settings.sinch_key_secret):
        return None
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("exp", 0) > now + 60:
        return _token_cache["token"]
    try:
        resp = httpx.post(_AUTH_URL, data={"grant_type": "client_credentials"},
                          auth=(settings.sinch_key_id, settings.sinch_key_secret), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["exp"] = now + int(data.get("expires_in", 3600))
        return _token_cache["token"]
    except Exception as e:  # noqa: BLE001
        logger.error("[sinch] no se pudo obtener token: %s", e)
        return None


def send_text(channel: str, recipient: str, text: str) -> dict:
    """Envía un texto por Instagram/Messenger vía Sinch (identidad del canal)."""
    token = _get_token()
    if not token:
        logger.info("[sinch] sin credenciales; no se envía (canal=%s)", channel)
        return {"ok": False, "error": "sinch no configurado"}
    payload = {
        "app_id": settings.sinch_app_id,
        "recipient": {"identified_by": {"channel_identities": [
            {"channel": _SINCH_CHANNEL.get(channel, (channel or "").upper()), "identity": recipient}]}},
        "message": {"text_message": {"text": text}},
    }
    resp = httpx.post(f"{_base_url()}/messages:send",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      json=payload, timeout=20)
    if not resp.is_success:
        raise Exception(f"Sinch HTTP {resp.status_code}: {resp.text}")
    return resp.json() if resp.content else {"ok": True}


def verify_signature(raw_body: bytes, headers) -> bool:
    """Verifica la firma HMAC-SHA256 (base64) del webhook de Sinch. Sin secret configurado, acepta."""
    secret = settings.sinch_webhook_secret
    if not secret:
        return True
    sig = headers.get("x-sinch-webhook-signature") or headers.get("x-sinch-signature") or ""
    if not sig:
        return False
    nonce = headers.get("x-sinch-webhook-signature-nonce", "") or ""
    ts = headers.get("x-sinch-webhook-signature-timestamp", "") or ""
    body = raw_body.decode("utf-8", "ignore")
    candidatos = []
    if nonce or ts:
        candidatos += [f"{body}.{nonce}.{ts}", f"{body}{nonce}{ts}"]
    candidatos.append(body)
    for data in candidatos:
        computed = base64.b64encode(hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest()).decode()
        if hmac.compare_digest(computed, sig):
            return True
    return False
