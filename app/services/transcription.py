"""Transcripción de notas de voz (Whisper). Port de bienesraicesEnrique.

Devuelve None si algo falla — un audio nunca debe tumbar el flujo del bot.
"""
import logging

import httpx
from openai import OpenAI

from app.config import settings

logger = logging.getLogger("transcription")
_MODEL = "whisper-1"


def transcribir(url: str) -> str | None:
    """Descarga el audio de `url` y lo pasa a texto. None si falla."""
    if not settings.openai_api_key or not url:
        return None
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        audio = resp.content
        if not audio:
            return None
        client = OpenAI(api_key=settings.openai_api_key, timeout=40)
        content_type = resp.headers.get("content-type", "audio/ogg")
        r = client.audio.transcriptions.create(model=_MODEL, file=("audio.ogg", audio, content_type))
        return (r.text or "").strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo transcribir el audio %s", url)
        return None
