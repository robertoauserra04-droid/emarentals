"""Fases del pipeline (columnas del Kanban) — configurables y adaptables.

El admin renombra, recolora, reordena y agrega/quita columnas. Las 6 fases base tienen `rol`
fijo (el bot las usa por su `clave`); las que agrega el admin son `custom` (manuales).
"""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import EmaLead, Fase
from app.models.user import User
from app.services import auth

router = APIRouter(prefix="/api/fases", tags=["fases"])

# Fases base (clave estable, nombre, color, rol). Se siembran una vez. Basadas en el boceto EMA:
# Nuevo → Interesado (el bot recupera) → Low Priority → Residencial Bueno / Oficina Bueno → cierre.
FASES_DEFAULT = [
    ("nuevo",             "Nuevo",             "#6b8ba4", "entrada"),
    ("interesado",        "Interesado",        "#5b7fb0", "pipeline"),
    ("low_priority",      "Low Priority",      "#9a9188", "lowpri"),
    ("residencial_bueno", "Residencial Bueno", "#5b8f6a", "bueno_residencial"),
    ("oficina_bueno",     "Oficina Bueno",     "#3f7d55", "bueno_oficina"),
    ("ganado",            "Ganado",            "#2f6647", "ganado"),
    ("perdido",           "Perdido",           "#a9603f", "perdido"),
]


def seed_fases(db: Session) -> None:
    if db.query(Fase).count() == 0:
        for i, (clave, nombre, color, rol) in enumerate(FASES_DEFAULT):
            db.add(Fase(clave=clave, nombre=nombre, color=color, orden=i, rol=rol, activa=True))
        db.commit()


def _dto(f: Fase) -> dict:
    return {"id": f.id, "clave": f.clave, "nombre": f.nombre, "color": f.color,
            "orden": f.orden, "rol": f.rol, "activa": bool(f.activa),
            "editable_borrar": f.rol == "custom"}


def listar_fases(db: Session) -> list[Fase]:
    seed_fases(db)
    return db.query(Fase).filter(Fase.activa == True).order_by(Fase.orden.asc()).all()  # noqa: E712


def clave_por_rol(db: Session, rol: str, default: str) -> str:
    f = db.query(Fase).filter(Fase.rol == rol, Fase.activa == True).first()  # noqa: E712
    return f.clave if f else default


@router.get("")
def listar(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    return [_dto(f) for f in listar_fases(db)]


class FaseIn(BaseModel):
    nombre: str
    color: str | None = None


@router.post("")
def crear(data: FaseIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Agrega una columna nueva (rol custom, manual)."""
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(422, "El nombre es obligatorio")
    base = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_") or "fase"
    clave = base
    n = 1
    while db.query(Fase).filter(Fase.clave == clave).first():
        n += 1
        clave = f"{base}_{n}"
    orden = (db.query(Fase).count())
    f = Fase(clave=clave, nombre=nombre, color=data.color or "#8c725d", orden=orden,
             rol="custom", activa=True)
    db.add(f)
    db.commit()
    return _dto(f)


@router.put("/{fase_id}")
def editar(fase_id: int, data: FaseIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Renombra / recolora una fase (la clave y el rol NO cambian)."""
    f = db.query(Fase).filter(Fase.id == fase_id).first()
    if not f:
        raise HTTPException(404, "Fase no encontrada")
    if data.nombre.strip():
        f.nombre = data.nombre.strip()
    if data.color:
        f.color = data.color
    db.commit()
    return _dto(f)


class OrdenIn(BaseModel):
    ids: list[int]   # ids en el orden deseado


@router.post("/reordenar")
def reordenar(data: OrdenIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    for i, fid in enumerate(data.ids):
        f = db.query(Fase).filter(Fase.id == fid).first()
        if f:
            f.orden = i
    db.commit()
    return {"ok": True}


@router.delete("/{fase_id}")
def borrar(fase_id: int, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Borra una columna custom; sus leads se mueven a la fase de entrada. Las base no se borran."""
    f = db.query(Fase).filter(Fase.id == fase_id).first()
    if not f:
        raise HTTPException(404, "Fase no encontrada")
    if f.rol != "custom":
        raise HTTPException(400, "Las fases base no se pueden borrar (solo renombrar/recolorar)")
    entrada = clave_por_rol(db, "entrada", "nuevo")
    db.query(EmaLead).filter(EmaLead.estado == f.clave).update({EmaLead.estado: entrada})
    db.delete(f)
    db.commit()
    return {"ok": True}
