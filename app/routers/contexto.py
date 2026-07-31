"""Contexto del bot — CRUD desde el panel. El bot lee los activos en cada turno.

Cualquier persona con la sección 'contexto' puede ver; crear/editar/borrar exige ser dueño.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import ContextoBot
from app.models.user import User
from app.services import auth

router = APIRouter(prefix="/api/contexto", tags=["contexto"])

_ver = auth.requiere_seccion("contexto")


class ContextoIn(BaseModel):
    titulo: str
    contenido: str
    activo: bool = True


def _dto(c: ContextoBot) -> dict:
    return {"id": c.id, "titulo": c.titulo, "contenido": c.contenido, "activo": bool(c.activo),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


@router.get("")
def listar(db: Session = Depends(get_db), user: User = Depends(_ver)):
    filas = db.query(ContextoBot).order_by(ContextoBot.id.asc()).all()
    return [_dto(c) for c in filas]


@router.post("")
def crear(data: ContextoIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    if not data.titulo.strip() or not data.contenido.strip():
        raise HTTPException(422, "Título y contenido son obligatorios")
    c = ContextoBot(titulo=data.titulo.strip(), contenido=data.contenido.strip(), activo=data.activo)
    db.add(c)
    db.commit()
    return _dto(c)


@router.put("/{cid}")
def editar(cid: int, data: ContextoIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    c = db.query(ContextoBot).filter(ContextoBot.id == cid).first()
    if not c:
        raise HTTPException(404, "Contexto no encontrado")
    c.titulo = data.titulo.strip()
    c.contenido = data.contenido.strip()
    c.activo = data.activo
    db.commit()
    return _dto(c)


@router.post("/{cid}/toggle")
def toggle(cid: int, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    c = db.query(ContextoBot).filter(ContextoBot.id == cid).first()
    if not c:
        raise HTTPException(404, "Contexto no encontrado")
    c.activo = not c.activo
    db.commit()
    return _dto(c)


@router.delete("/{cid}")
def borrar(cid: int, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    c = db.query(ContextoBot).filter(ContextoBot.id == cid).first()
    if not c:
        raise HTTPException(404, "Contexto no encontrado")
    db.delete(c)
    db.commit()
    return {"ok": True}
