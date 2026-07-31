"""Verificación de firma HMAC del webhook de Kapso (seguridad día 1).

Kapso firma en el header `X-Webhook-Signature` con HMAC-SHA256 en HEX plano (Meta usa el prefijo
`sha256=`; algunos emisores firman en base64). Aceptamos las tres formas. En prod la firma SIEMPRE
se verifica; en dev, sin secreto, se deja pasar con un warning para poder probar.
"""
import base64
import hashlib
import hmac
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def verificar_firma(body: bytes, firma_header: str | None) -> bool:
    secret = settings.kapso_webhook_secret
    if not secret:
        if settings.env == "prod":
            return False  # en prod nunca sin firma
        logger.warning("[webhook] sin KAPSO_WEBHOOK_SECRET (dev): se acepta sin verificar")
        return True
    if not firma_header:
        return False
    sig = firma_header.strip()
    if sig.lower().startswith("sha256="):
        sig = sig[len("sha256="):]
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    esperado_hex = digest.hex()
    esperado_b64 = base64.b64encode(digest).decode()
    try:
        if hmac.compare_digest(sig.lower(), esperado_hex.lower()):
            return True
        return hmac.compare_digest(sig, esperado_b64)
    except Exception:  # noqa: BLE001
        return False
