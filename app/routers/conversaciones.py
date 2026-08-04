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
from app.services import auth, messaging_out, visibilidad

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
    # Fuera de la bandeja: leads ocultos y contactos marcados "no es lead". Su conversación sigue
    # guardada — se consulta desde Historial.
    fuera = {p for p, l in leads.items() if l.oculto} | visibilidad.telefonos_no_lead(db)
    out = []
    for c in db.query(Conversation).order_by(Conversation.last_message_at.desc()).all():
        if permitidos is not None and c.phone not in permitidos:
            continue
        if c.phone in fuera:
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


@router.post("/{phone}/reiniciar")
def reiniciar(phone: str, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Empieza la conversación de nuevo (para pruebas): borra el historial y deja el lead como nuevo,
    con el bot activo. El siguiente mensaje arranca el flujo desde cero."""
    db.query(ChatMessage).filter(ChatMessage.phone == phone).delete(synchronize_session=False)
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    if lead:
        for campo in ("tipo_propiedad", "recamaras", "oficina_m2", "oficina_personas", "tiempo_renta",
                      "tipo_oficina", "marca", "modelo", "uso", "zona", "presupuesto", "resumen",
                      "que_pregunto", "nivel_interes", "motivo_perdida"):
            setattr(lead, campo, None)
        lead.estado = "nuevo"
        lead.es_buen_prospecto = False
        lead.score_calif = 0
        lead.escalated = False
        lead.alertado_at = None
        lead.message_count = 0
        lead.bot_active = True
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    if conv:
        conv.bot_active = True
    db.commit()
    return {"ok": True}


class SimIn(BaseModel):
    texto: str


@router.post("/{phone}/simular")
def simular(phone: str, body: SimIn, db: Session = Depends(get_db),
            user: User = Depends(auth.current_user)):
    """Modo prueba: mete un mensaje COMO CLIENTE y corre el bot SIN mandar nada real por el canal.
    Sirve para probar internamente el flujo y ver cómo clasifica/responde el bot."""
    texto = (body.texto or "").strip()
    if not texto:
        raise HTTPException(422, "Texto vacío")
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    channel = (conv.channel if conv else None) or "whatsapp"
    if conv is None:
        conv = Conversation(phone=phone, channel=channel)
        db.add(conv)
    conv.bot_active = True   # asegurar que el bot conteste en la prueba
    db.add(ChatMessage(phone=phone, channel=channel, direction=MessageDirection.inbound, body=texto))
    db.commit()
    from app.services import recovery
    from app.services.bot import handler, leads as leadsvc
    lead = leadsvc.get_or_create_lead(db, phone, channel=channel)
    lead.bot_active = True
    recovery.register_inbound(db, lead, texto)
    db.commit()
    handler.handle_inbound(db, phone, texto, channel=channel, enviar=False)  # dry-run
    return {"ok": True}


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
