"""Verificación de firma HMAC del webhook de Kapso (seguridad día 1).

En prod la firma SIEMPRE se verifica (config.validar_config aborta el arranque si falta el
secreto). En dev, si no hay secreto configurado, se deja pasar con un warning para poder probar.
"""
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
    esperado = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # el header puede venir como "sha256=<hex>"
    recibido = firma_header.split("=", 1)[-1].strip()
    return hmac.compare_digest(esperado, recibido)
