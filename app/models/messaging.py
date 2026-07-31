"""Conversación + mensajes de chat (bandeja del panel). Reuso simplificado de vella panel."""
import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class MessageDirection(str, enum.Enum):
    inbound = "inbound"     # del prospecto
    outbound = "outbound"   # del bot / asesor


class Conversation(Base):
    __tablename__ = "conversations"

    id              = Column(Integer, primary_key=True)
    phone           = Column(String, unique=True, index=True, nullable=False)
    name            = Column(String, nullable=True)
    channel         = Column(String, default="whatsapp")
    bot_active      = Column(Boolean, default=True)   # coexistencia: off => el humano tomó el chat
    last_message_at = Column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id         = Column(Integer, primary_key=True)
    phone      = Column(String, index=True, nullable=False)
    channel    = Column(String, default="whatsapp")
    direction  = Column(Enum(MessageDirection), nullable=False)
    body       = Column(Text, nullable=True)
    wamid      = Column(String, index=True, nullable=True)  # id del mensaje (dedup anti-duplicado)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
