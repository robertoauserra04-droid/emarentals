"""Leads / pipeline (kanban) — la vista principal del panel de EMA Rentals.

Filtro de leads: el dueño/admin ve todos; el asesor también (no hay round-robin, la asignación
es informal por la alerta). Sin finanzas: cerrar como ganado/perdido es solo informativo.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import ESTADOS_VALIDOS, EmaLead
from app.models.lead_evento import LeadEvento, LeadNota
from app.models.messaging import ChatMessage, MessageDirection
from app.models.user import User
from app.services import auth, eventos

router = APIRouter(prefix="/api/leads", tags=["leads"])

_ORDEN = ["nuevo", "interesado", "calificado", "asignado", "ganado", "perdido"]


def _lead_dto(l: EmaLead) -> dict:
    return {
        "id": l.id, "phone": l.phone, "name": l.name, "estado": l.estado,
        "score_calif": l.score_calif or 0, "nivel_interes": l.nivel_interes,
        "marca": l.marca, "modelo": l.modelo,
        "segmento": l.segmento, "necesidad": l.necesidad, "plazo_meses": l.plazo_meses,
        "fecha_entrega": l.fecha_entrega, "zona": l.zona, "presupuesto": l.presupuesto,
        "resumen": l.resumen, "que_pregunto": l.que_pregunto, "source": l.source,
        "message_count": l.message_count, "monto_cierre": l.monto_cierre,
        "alertado": l.alertado_at is not None,
        "last_message_at": l.last_message_at.isoformat() if l.last_message_at else None,
    }


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Kanban agrupado por etapa."""
    columnas = {e: [] for e in _ORDEN}
    for l in db.query(EmaLead).order_by(EmaLead.last_message_at.desc()).all():
        columnas.get(l.estado or "nuevo", columnas["nuevo"]).append(_lead_dto(l))
    return {"columnas": _ORDEN, "leads": columnas}


@router.get("/{lead_id}")
def detalle(lead_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    return _lead_dto(lead)


@router.get("/{lead_id}/mensajes")
def mensajes(lead_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Timeline de la conversación del lead (qué escribió y qué respondió el bot/asesor)."""
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    msgs = (db.query(ChatMessage).filter(ChatMessage.phone == lead.phone)
            .order_by(ChatMessage.created_at.asc()).all())
    return [{"dir": "in" if m.direction == MessageDirection.inbound else "out",
             "body": m.body, "at": m.created_at.isoformat() if m.created_at else None}
            for m in msgs]


@router.put("/{lead_id}/estado")
def cambiar_estado(lead_id: int, estado: str, db: Session = Depends(get_db),
                   user: User = Depends(auth.current_user)):
    """Cambia la etapa (drag & drop del kanban). Solo informativo (sin finanzas)."""
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(400, "Estado inválido")
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    prev = lead.estado
    lead.estado = estado
    if estado == "ganado":
        lead.es_venta = True
    autor = user.nombre or user.email
    if prev != estado:
        eventos.registrar(db, lead.id, "etapa", f"Etapa: {prev or 'nuevo'} → {estado}", autor)
    db.commit()
    return {"ok": True, "estado": estado}


# ─────────── Bitácora (eventos) + notas del lead ───────────

@router.get("/{lead_id}/actividad")
def actividad(lead_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    evs = (db.query(LeadEvento).filter(LeadEvento.lead_id == lead_id)
           .order_by(LeadEvento.created_at.desc()).all())
    notas = (db.query(LeadNota).filter(LeadNota.lead_id == lead_id)
             .order_by(LeadNota.created_at.desc()).all())
    return {
        "eventos": [{"tipo": e.tipo, "detalle": e.detalle, "autor": e.autor,
                     "at": e.created_at.isoformat() if e.created_at else None} for e in evs],
        "notas": [{"id": n.id, "autor": n.autor, "texto": n.texto,
                   "at": n.created_at.isoformat() if n.created_at else None} for n in notas],
    }


class NotaIn(BaseModel):
    texto: str


@router.post("/{lead_id}/notas")
def agregar_nota(lead_id: int, body: NotaIn, db: Session = Depends(get_db),
                 user: User = Depends(auth.current_user)):
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    autor = user.nombre or user.email
    db.add(LeadNota(lead_id=lead_id, autor=autor, texto=body.texto))
    eventos.registrar(db, lead_id, "nota", "Agregó una nota", autor)
    db.commit()
    return {"ok": True}
