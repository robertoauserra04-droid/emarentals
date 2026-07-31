"""Leads / pipeline (kanban) — la vista principal del panel de EMA Rentals.

Filtro de leads: el dueño/admin ve todos; el asesor también (no hay round-robin, la asignación
es informal por la alerta). Sin finanzas: cerrar como ganado/perdido es solo informativo.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import EmaLead
from app.models.lead_evento import LeadEvento, LeadNota
from app.models.messaging import ChatMessage, MessageDirection
from app.models.user import User
from app.routers.fases import listar_fases
from app.services import auth, clasificacion, eventos

router = APIRouter(prefix="/api/leads", tags=["leads"])

_PERFILES = ["socio_estrategico", "aliado_operativo", "cliente_premium", "cliente_estandar", "sin_clasificar"]


def _lead_dto(l: EmaLead) -> dict:
    return {
        "id": l.id, "phone": l.phone, "name": l.name, "estado": l.estado,
        "score_calif": l.score_calif or 0, "nivel_interes": l.nivel_interes,
        "marca": l.marca, "modelo": l.modelo,
        # Flujo de calificación (renta)
        "tipo_propiedad": l.tipo_propiedad, "recamaras": l.recamaras,
        "oficina_m2": l.oficina_m2, "oficina_personas": l.oficina_personas,
        "tiempo_renta": l.tiempo_renta, "tipo_oficina": l.tipo_oficina,
        "es_buen_prospecto": bool(l.es_buen_prospecto),
        "segmento": l.segmento, "necesidad": l.necesidad, "plazo_meses": l.plazo_meses,
        "fecha_entrega": l.fecha_entrega, "zona": l.zona, "presupuesto": l.presupuesto,
        "resumen": l.resumen, "que_pregunto": l.que_pregunto, "source": l.source,
        "message_count": l.message_count, "monto_cierre": l.monto_cierre,
        "alertado": l.alertado_at is not None,
        # Perfil estratégico (cuadrante)
        "perfil": l.perfil or "sin_clasificar", "horas_estimadas": l.horas_estimadas or 0,
        "ticket_mensual": l.ticket_mensual, "uso": l.uso,
        "potencial_escala": l.potencial_escala, "urgencia_cierre": l.urgencia_cierre,
        "estructura": l.estructura,
        "last_message_at": l.last_message_at.isoformat() if l.last_message_at else None,
    }


@router.get("/pipeline")
def pipeline(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Kanban agrupado por fase (columnas/orden/color vienen de las fases configurables)."""
    fs = listar_fases(db)
    claves = [f.clave for f in fs]
    fallback = claves[0] if claves else "nuevo"
    columnas = {c: [] for c in claves}
    for l in db.query(EmaLead).order_by(EmaLead.last_message_at.desc()).all():
        col = l.estado if l.estado in columnas else fallback
        columnas[col].append(_lead_dto(l))
    return {
        "columnas": claves,
        "fases": {f.clave: {"nombre": f.nombre, "color": f.color,
                            "descripcion": f.descripcion or "", "criterios": f.criterios or ""} for f in fs},
        "leads": columnas,
    }


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
    claves = {f.clave for f in listar_fases(db)}
    if estado not in claves:
        raise HTTPException(400, "Fase inválida")
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


# ─────────── Perfil estratégico (cuadrante 2×2) ───────────

class PerfilIn(BaseModel):
    ticket_mensual: int | None = None
    uso: str | None = None                # reventa | propio
    potencial_escala: str | None = None   # 1-3 | 4-6 | 7-15 | +15
    urgencia_cierre: str | None = None    # ya | media | baja
    estructura: str | None = None         # persona_fisica | empresa


@router.put("/{lead_id}/perfil")
def guardar_perfil(lead_id: int, data: PerfilIn, db: Session = Depends(get_db),
                   user: User = Depends(auth.current_user)):
    """Guarda las variables del perfil y recalcula (perfil + horas) al momento."""
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    for campo in ("ticket_mensual", "uso", "potencial_escala", "urgencia_cierre", "estructura"):
        val = getattr(data, campo)
        if val is not None:
            setattr(lead, campo, val)
    perfil, horas = clasificacion.clasificar(db, lead)
    db.commit()
    return {"ok": True, "perfil": perfil, "horas_estimadas": horas, **_lead_dto(lead)}


@router.get("/resumen/cuadrante")
def resumen_cuadrante(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Vista Resumen: cuadrante 2×2 con conteos, horas totales y el umbral vigente."""
    leads = db.query(EmaLead).all()
    por_perfil = {p: {"count": 0, "horas": 0, "leads": []} for p in _PERFILES}
    for l in leads:
        p = l.perfil or "sin_clasificar"
        if p not in por_perfil:
            p = "sin_clasificar"
        por_perfil[p]["count"] += 1
        por_perfil[p]["horas"] += (l.horas_estimadas or 0)
        por_perfil[p]["leads"].append(_lead_dto(l))
    return {
        "umbral": int(clasificacion.get_umbral(db)),
        "perfiles": {p: {"label": clasificacion.PERFILES[p]["label"],
                         "emoji": clasificacion.PERFILES[p]["emoji"],
                         "color": clasificacion.PERFILES[p]["color"],
                         "count": por_perfil[p]["count"],
                         "horas": por_perfil[p]["horas"],
                         "leads": por_perfil[p]["leads"]} for p in _PERFILES},
        "horas_totales": sum(v["horas"] for v in por_perfil.values()),
        "total": len(leads),
    }


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
