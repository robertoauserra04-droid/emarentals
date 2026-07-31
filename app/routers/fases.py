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

# Fases base (clave, nombre, color, rol, descripción, criterios). Basadas en el boceto EMA:
# Nuevo → Interesado (el bot recupera) → Low Priority → Residencial Bueno / Oficina Bueno → cierre.
FASES_DEFAULT = [
    ("nuevo", "Nuevo", "#6b8ba4", "entrada",
     "Lead recién llegado del anuncio; aún no responde las preguntas de filtrado.",
     "Entra aquí automáticamente en cuanto escribe por primera vez."),
    ("interesado", "Interesado", "#5b7fb0", "pipeline",
     "Está respondiendo pero falta información para calificarlo. El bot le da seguimiento.",
     "Dio el tipo de propiedad o mostró interés, pero falta el dato ligado (recámaras, m² o personas) o el tiempo de renta."),
    ("low_priority", "Low Priority", "#9a9188", "lowpri",
     "Prospecto completo que no cumple el criterio de buen prospecto.",
     "Ya dio tipo + su dato + tiempo, pero no llega al umbral: departamento de 1 recámara, u oficina menor a 100 m² y menos de 20 personas."),
    ("residencial_bueno", "Residencial Bueno", "#5b8f6a", "bueno_residencial",
     "Buen prospecto residencial (casa o departamento). Se avisa a un asesor.",
     "Casa (cualquiera), o departamento con 2 o más recámaras, con el tiempo de renta definido."),
    ("oficina_bueno", "Oficina Bueno", "#3f7d55", "bueno_oficina",
     "Buen prospecto de oficina. Se avisa a un asesor.",
     "Oficina de 100 m² o más, o de 20 personas o más, con tiempo definido. Tipo 1 = hasta 1 año, Tipo 2 = más de 1 año."),
    ("ganado", "Ganado", "#2f6647", "ganado",
     "El prospecto cerró con EMA.",
     "El asesor lo marca manualmente al concretar la renta."),
    ("perdido", "Perdido", "#a9603f", "perdido",
     "No avanzó o perdió interés.",
     "Se marca cuando el prospecto declina o deja de contestar."),
]
_DESC = {c: (d, cr) for (c, _n, _col, _r, d, cr) in FASES_DEFAULT}


def seed_fases(db: Session) -> None:
    if db.query(Fase).count() == 0:
        for i, (clave, nombre, color, rol, desc, crit) in enumerate(FASES_DEFAULT):
            db.add(Fase(clave=clave, nombre=nombre, color=color, orden=i, rol=rol, activa=True,
                        descripcion=desc, criterios=crit))
        db.commit()
    else:
        # Backfill: fases base ya creadas sin descripción/criterios (deploy previo) → rellenar.
        cambios = False
        for f in db.query(Fase).filter(Fase.clave.in_(list(_DESC.keys()))).all():
            if not f.descripcion and not f.criterios:
                f.descripcion, f.criterios = _DESC[f.clave]
                cambios = True
        if cambios:
            db.commit()


def _dto(f: Fase) -> dict:
    return {"id": f.id, "clave": f.clave, "nombre": f.nombre, "color": f.color,
            "orden": f.orden, "rol": f.rol, "activa": bool(f.activa),
            "descripcion": f.descripcion or "", "criterios": f.criterios or "",
            "editable_borrar": True}


# Rol semántico de cada clave base (para rutear con fallback si borran la fase destino).
_ROL_DE_CLAVE = {
    "nuevo": "entrada", "interesado": "pipeline", "low_priority": "lowpri",
    "residencial_bueno": "bueno_residencial", "oficina_bueno": "bueno_oficina",
    "ganado": "ganado", "perdido": "perdido",
}
_FALLBACK_ROL = {
    "bueno_oficina": ["bueno_residencial", "entrada"], "bueno_residencial": ["bueno_oficina", "entrada"],
    "lowpri": ["entrada"], "pipeline": ["entrada"], "perdido": ["entrada"], "ganado": ["entrada"],
    "entrada": [],
}


def resolver_clave(db: Session, clave: str | None) -> str:
    """Devuelve una clave de fase EXISTENTE. Si la deseada fue borrada, cae a una equivalente por
    rol (buen prospecto → otra 'bueno' o a la entrada). Así borrar cualquier fase no rompe el bot."""
    fs = listar_fases(db)
    claves = {f.clave for f in fs}
    if clave in claves:
        return clave
    porrol = {f.rol: f.clave for f in fs}
    rol = _ROL_DE_CLAVE.get(clave or "", "")
    for r in ([rol] + _FALLBACK_ROL.get(rol, [])):
        if r in porrol:
            return porrol[r]
    return fs[0].clave if fs else (clave or "nuevo")


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
    descripcion: str | None = None
    criterios: str | None = None


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
    if data.descripcion is not None:
        f.descripcion = data.descripcion.strip()
    if data.criterios is not None:
        f.criterios = data.criterios.strip()
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
    """Borra CUALQUIER fase. Sus leads se mueven a otra fase (la de entrada, o la primera que quede).
    No se puede borrar la última (debe quedar al menos una columna)."""
    f = db.query(Fase).filter(Fase.id == fase_id).first()
    if not f:
        raise HTTPException(404, "Fase no encontrada")
    if db.query(Fase).count() <= 1:
        raise HTTPException(400, "Debe quedar al menos una fase en el pipeline")
    # Destino de los leads: la fase de entrada si no es la que se borra; si no, la primera que quede.
    destino = None
    entrada = db.query(Fase).filter(Fase.rol == "entrada", Fase.id != f.id).first()
    if entrada:
        destino = entrada.clave
    else:
        otra = db.query(Fase).filter(Fase.id != f.id).order_by(Fase.orden.asc()).first()
        destino = otra.clave if otra else None
    if destino:
        db.query(EmaLead).filter(EmaLead.estado == f.clave).update({EmaLead.estado: destino})
    db.delete(f)
    db.commit()
    return {"ok": True, "movidos_a": destino}
