"""Leads / pipeline (kanban) — la vista principal del panel de EMA Rentals.

Filtro de leads: el dueño/admin ve todos; el asesor también (no hay round-robin, la asignación
es informal por la alerta). Sin finanzas: cerrar como ganado/perdido es solo informativo.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import ContactoNoLead, EmaLead
from app.models.lead_evento import LeadEvento, LeadNota
from app.models.messaging import ChatMessage, MessageDirection
from app.models.user import User
from app.routers.fases import listar_fases
from app.services.bot import leads as leads_svc
from app.services import auth, clasificacion, eventos, visibilidad

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
        # De dónde salió el score y qué preguntas siguen sin responder (el panel lo muestra para
        # que el número no sea opaco, que era el problema del score anterior).
        "score_desglose": leads_svc.desglose_score(l),
        "falta_cuestionario": leads_svc.falta_del_cuestionario(l),
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
    q = visibilidad.visibles(db.query(EmaLead))
    for l in q.order_by(EmaLead.last_message_at.desc()).all():
        col = l.estado if l.estado in columnas else fallback
        columnas[col].append(_lead_dto(l))
    return {
        "columnas": claves,
        # `id` y `mensaje_cierre` los usa el panel de la ⓘ de cada columna, que es donde se
        # editan los criterios y el mensaje de cierre (la pantalla de Fases solo lleva toggles).
        "fases": {f.clave: {"id": f.id, "nombre": f.nombre, "color": f.color,
                            "descripcion": f.descripcion or "", "criterios": f.criterios or "",
                            "mensaje_cierre": f.mensaje_cierre or ""} for f in fs},
        "leads": columnas,
    }


# ─────────── Historial y visibilidad ───────────
# OJO: estas rutas literales van ANTES de `GET /{lead_id}`, o FastAPI intenta parsear "historial"
# como int y truena.

@router.get("/historial")
def historial(search: str = "", filtro: str = "todos", limit: int = 60, offset: int = 0,
              db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Todos los contactos que han existido, incluidos los ocultos y los marcados 'no es lead'.

    `filtro`: todos | activos | ocultos | no_lead
    """
    no_lead = visibilidad.telefonos_no_lead(db)
    q = db.query(EmaLead)
    if search.strip():
        patron = f"%{search.strip()}%"
        q = q.filter(or_(EmaLead.name.ilike(patron), EmaLead.phone.ilike(patron)))
    if filtro == "ocultos":
        q = q.filter(EmaLead.oculto.is_(True))
    elif filtro == "no_lead":
        q = q.filter(EmaLead.phone.in_(no_lead) if no_lead else False)
    elif filtro == "activos":
        q = visibilidad.visibles(q)

    total = q.count()
    filas = q.order_by(EmaLead.last_message_at.desc().nullslast(),
                       EmaLead.id.desc()).limit(min(limit, 200)).offset(offset).all()
    fases = {f.clave: f.nombre for f in listar_fases(db)}
    return {
        "total": total, "limit": limit, "offset": offset,
        "contactos": [{
            "id": l.id, "phone": l.phone, "name": l.name, "source": l.source,
            "estado": l.estado, "fase": fases.get(l.estado or "", l.estado or ""),
            "score_calif": l.score_calif or 0, "message_count": l.message_count or 0,
            "oculto": bool(l.oculto), "no_lead": l.phone in no_lead,
            "tipo_propiedad": l.tipo_propiedad,
            "created_at": l.created_at.isoformat() if l.created_at else None,
            "last_message_at": l.last_message_at.isoformat() if l.last_message_at else None,
        } for l in filas],
    }


@router.post("/{lead_id}/ocultar")
def ocultar(lead_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    """Saca el lead del tablero SIN borrar nada. Se recupera desde Historial."""
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    lead.oculto = True
    lead.oculto_at = datetime.now(timezone.utc)
    eventos.registrar(db, lead.id, "oculto", "Quitado del tablero",
                      autor=user.nombre or user.email)
    db.commit()
    return {"ok": True}


@router.post("/{lead_id}/mostrar")
def mostrar(lead_id: int, db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    lead = db.query(EmaLead).filter(EmaLead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead no encontrado")
    lead.oculto = False
    lead.oculto_at = None
    eventos.registrar(db, lead.id, "oculto", "Devuelto al tablero",
                      autor=user.nombre or user.email)
    db.commit()
    return {"ok": True}


class NoLeadIn(BaseModel):
    telefono: str
    motivo: str | None = None


@router.get("/no-lead/lista")
def listar_no_lead(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    filas = db.query(ContactoNoLead).order_by(ContactoNoLead.id.desc()).all()
    return [{"id": c.id, "telefono": c.telefono, "motivo": c.motivo} for c in filas]


@router.post("/no-lead")
def marcar_no_lead(data: NoLeadIn, db: Session = Depends(get_db),
                   user: User = Depends(auth.current_user)):
    """Marca un teléfono como 'no es prospecto'. Permanente y por teléfono: sobrevive a que el
    lead se oculte o se reinicie. El bot deja de contestarle; su conversación se sigue guardando."""
    telefono = leads_svc.norm_phone(data.telefono.strip())
    if not telefono:
        raise HTTPException(422, "El teléfono es obligatorio")
    if not db.query(ContactoNoLead).filter(ContactoNoLead.telefono == telefono).first():
        db.add(ContactoNoLead(telefono=telefono, motivo=(data.motivo or "").strip() or None))
    # También lo sacamos del tablero: si no es lead, no debe ocupar una columna.
    lead = db.query(EmaLead).filter(EmaLead.phone == telefono).first()
    if lead:
        lead.oculto = True
        lead.oculto_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "telefono": telefono}


@router.delete("/no-lead/{telefono}")
def desmarcar_no_lead(telefono: str, db: Session = Depends(get_db),
                      user: User = Depends(auth.current_user)):
    """Vuelve a tratarlo como prospecto. No devuelve el lead al tablero por sí solo: eso es
    'Devolver al Kanban', para que sean dos decisiones separadas."""
    tel = leads_svc.norm_phone(telefono.strip())
    db.query(ContactoNoLead).filter(ContactoNoLead.telefono == tel).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}


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
    leads = visibilidad.leads_visibles(db).all()
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
