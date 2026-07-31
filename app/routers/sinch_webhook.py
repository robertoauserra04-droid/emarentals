"""Webhook de Sinch — mensajes entrantes de Instagram y Messenger.

Verifica la firma, normaliza el evento a {phone(identidad), texto, channel} y dispara el
mismo bot (handle_inbound) que WhatsApp. Así los 3 canales comparten cerebro y bandeja.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.services import recovery, sinch
from app.services.bot import handler, leads

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook/sinch", tags=["webhook"])

_CANAL = {"INSTAGRAM": "instagram", "MESSENGER": "messenger", "WHATSAPP": "whatsapp"}


def _extraer(payload: dict) -> dict | None:
    """Normaliza el evento de mensaje entrante de Sinch Conversation API."""
    try:
        msg = payload.get("message") or {}
        cm = msg.get("contact_message") or {}
        texto = (cm.get("text_message") or {}).get("text", "")
        audio_url = None
        media = cm.get("media_message") or {}
        if not texto and media.get("url") and "audio" in (media.get("content_type") or "audio"):
            audio_url = media.get("url")
        # identidad del canal (IG/Messenger usan un PSID, no un teléfono)
        ident = None
        ci = msg.get("channel_identity") or {}
        canal = _CANAL.get((ci.get("channel") or "").upper(), "instagram")
        ident = ci.get("identity")
        if not ident:  # respaldo: algunos payloads traen la identidad en el remitente
            ident = (payload.get("message", {}).get("sender_id")
                     or payload.get("contact_id"))
        if not ident or (not texto and not audio_url):
            return None
        return {"phone": ident, "text": texto, "channel": canal, "audio_url": audio_url}
    except (KeyError, TypeError, AttributeError):
        return None


@router.post("")
async def inbound(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not sinch.verify_signature(raw, request.headers):
        logger.warning("[sinch] firma inválida")
        return {"ok": False}
    payload = await request.json()
    # Sinch manda también callbacks de estado (delivery/read): solo nos interesa message_inbound.
    if payload.get("message_metadata") is None and "message" not in payload:
        return {"ok": True, "ignored": True}
    data = _extraer(payload)
    if not data:
        return {"ok": True, "ignored": True}

    phone, text, channel = data["phone"], data["text"], data["channel"]
    if not text and data.get("audio_url"):
        from app.services import transcription
        tr = transcription.transcribir(data["audio_url"])
        text = tr or "(nota de voz — no se pudo transcribir)"
    if not text:
        return {"ok": True, "ignored": True}
    if db.query(Conversation).filter(Conversation.phone == phone).first() is None:
        db.add(Conversation(phone=phone, channel=channel))
    db.add(ChatMessage(phone=phone, channel=channel, direction=MessageDirection.inbound, body=text))
    db.commit()

    lead = leads.get_or_create_lead(db, phone, channel=channel)
    recovery.register_inbound(db, lead, text)
    db.commit()

    handler.handle_inbound(db, phone, text, channel=channel)
    return {"ok": True}
