"""Conversaciones — bandeja de chat en vivo (como el WhatsApp de psicología).

Lista las conversaciones de todos los canales (WhatsApp/Instagram/Messenger), muestra el
historial, permite RESPONDER a mano y PAUSAR/REACTIVAR el bot por conversación (coexistencia).
El dueño ve todas; el asesor solo las de sus leads.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.models.user import User
from app.services import auth, messaging_out

router = APIRouter(prefix="/api/conversaciones", tags=["conversaciones"])


def _mis_telefonos(db: Session, user: User) -> set[str] | None:
    """Teléfonos que el usuario puede ver. None = todos.

    EMA Rentals no usa round-robin ni bandeja por asesor: admin y asesor ven todas las
    conversaciones (la asignación es informal, por la alerta de buen lead)."""
    return None


@router.get("")
def listar(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    permitidos = _mis_telefonos(db, user)
    leads = {l.phone: l for l in db.query(EmaLead).all()}
    out = []
    for c in db.query(Conversation).order_by(Conversation.last_message_at.desc()).all():
        if permitidos is not None and c.phone not in permitidos:
            continue
        total = db.query(ChatMessage).filter(ChatMessage.phone == c.phone).count()
        lead = leads.get(c.phone)
        out.append({
            "phone": c.phone, "name": c.name or (lead.name if lead else None),
            "channel": c.channel or "whatsapp", "bot_active": bool(c.bot_active),
            "estado": lead.estado if lead else None, "total": total,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
        })
    return out


@router.get("/{phone}")
def ver(phone: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    permitidos = _mis_telefonos(db, user)
    if permitidos is not None and phone not in permitidos:
        raise HTTPException(403, "No es tu conversación")
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    msgs = (db.query(ChatMessage).filter(ChatMessage.phone == phone)
            .order_by(ChatMessage.created_at.asc()).all())
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    return {
        "phone": phone, "name": (conv.name if conv else None) or (lead.name if lead else None),
        "channel": (conv.channel if conv else None) or "whatsapp",
        "bot_active": bool(conv.bot_active) if conv else True,
        "estado": lead.estado if lead else None,
        "mensajes": [{"dir": "in" if m.direction == MessageDirection.inbound else "out",
                      "body": m.body, "at": m.created_at.isoformat() if m.created_at else None}
                     for m in msgs],
    }


class EnviarIn(BaseModel):
    texto: str


@router.post("/{phone}/enviar")
def enviar(phone: str, body: EnviarIn, db: Session = Depends(get_db),
           user: User = Depends(auth.current_user)):
    permitidos = _mis_telefonos(db, user)
    if permitidos is not None and phone not in permitidos:
        raise HTTPException(403, "No es tu conversación")
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    channel = (conv.channel if conv else None) or "whatsapp"
    try:
        wamid = messaging_out.send_text(db, phone, body.texto, channel=channel)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Error al enviar por {channel}: {e}")
    return {"ok": True, "entregado": wamid is not None}


class BotIn(BaseModel):
    bot_active: bool | None = None


@router.post("/{phone}/bot")
def toggle_bot(phone: str, body: BotIn, db: Session = Depends(get_db),
               user: User = Depends(auth.current_user)):
    """Pausa/reactiva el bot en esta conversación (coexistencia: humano toma el chat)."""
    permitidos = _mis_telefonos(db, user)
    if permitidos is not None and phone not in permitidos:
        raise HTTPException(403, "No es tu conversación")
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    if not conv:
        conv = Conversation(phone=phone)
        db.add(conv)
    conv.bot_active = (not bool(conv.bot_active)) if body.bot_active is None else body.bot_active
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    if lead:
        lead.bot_active = conv.bot_active
    db.commit()
    return {"phone": phone, "bot_active": conv.bot_active}
