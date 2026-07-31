"""Configuración global del panel (por ahora: umbral de ticket del cuadrante)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services import auth, clasificacion

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/umbral")
def get_umbral(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    return {"ticket_umbral": int(clasificacion.get_umbral(db))}


class UmbralIn(BaseModel):
    ticket_umbral: int


@router.put("/umbral")
def put_umbral(data: UmbralIn, db: Session = Depends(get_db),
               user: User = Depends(auth.solo_dueno)):
    if data.ticket_umbral < 0:
        raise HTTPException(422, "El umbral debe ser positivo")
    clasificacion.set_umbral(db, float(data.ticket_umbral))
    # Reclasificar todos los leads con el nuevo umbral.
    from app.models.lead import EmaLead
    for l in db.query(EmaLead).all():
        clasificacion.clasificar(db, l)
    db.commit()
    return {"ok": True, "ticket_umbral": data.ticket_umbral}
