"""Envío saliente + persistencia en la bandeja del panel.

`send_text(db, phone, body, ...)`: manda por el canal correcto (WhatsApp→Kapso;
Instagram/Messenger→Sinch) y, si `db` no es None, registra el mensaje saliente y actualiza la
conversación (para el panel). `db=None` = envío sin persistir (p.ej. la alerta a un admin, que no
es una conversación con prospecto).
"""
import logging

from sqlalchemy.orm import Session

from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.services import kapso, sinch

logger = logging.getLogger(__name__)


def _enviar_por_canal(phone: str, body: str, channel: str) -> str | None:
    """WhatsApp → Kapso; Instagram/Messenger → Sinch. Devuelve un id de mensaje si aplica."""
    if channel in ("instagram", "messenger"):
        res = sinch.send_text(channel, phone, body)
        return res.get("message_id") if isinstance(res, dict) else None
    return kapso.send_text_sync(phone, body)


def _upsert_conversation(db: Session, phone: str, name: str | None, channel: str) -> None:
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    if conv is None:
        conv = Conversation(phone=phone, name=name, channel=channel)
        db.add(conv)
    if name and not conv.name:
        conv.name = name


def send_text(db: Session | None, phone: str, body: str, channel: str = "whatsapp",
              name: str | None = None) -> str | None:
    wamid = _enviar_por_canal(phone, body, channel)
    if db is not None:
        _upsert_conversation(db, phone, name, channel)
        db.add(ChatMessage(phone=phone, channel=channel,
                           direction=MessageDirection.outbound, body=body))
        db.commit()
    return wamid
