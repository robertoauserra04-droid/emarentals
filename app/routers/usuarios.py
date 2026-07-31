"""Gestión de usuarios del panel (rol + secciones granulares). Solo dueño escribe.

Patrón portado de Regional México: crear / editar acceso (rol + secciones) / activar-desactivar /
resetear contraseña. Sin DELETE físico: soft-disable (activo=False). Reglas de seguridad:
no desactivarte a ti mismo, min 6 chars de contraseña, email único.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services import auth, permisos

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"])

# Ver la sección "usuarios"; crear/editar exige además ser dueño.
_ver = auth.requiere_seccion("usuarios")


class UsuarioIn(BaseModel):
    email: str
    nombre: str | None = None
    password: str
    rol: str = "asesor"
    secciones: list[str] | None = None
    asesor_id: int | None = None


class AccesoIn(BaseModel):
    rol: str
    secciones: list[str] | None = None
    asesor_id: int | None = None


class PasswordIn(BaseModel):
    password: str


def _salida(u: User) -> dict:
    return {"id": u.id, "email": u.email, "nombre": u.nombre, "rol": u.rol,
            "secciones": permisos.secciones_de(u), "activo": u.activo, "asesor_id": u.asesor_id}


def _valida_rol(rol: str) -> None:
    if rol not in ("dueno", "coordinador", "asesor"):
        raise HTTPException(422, "Rol inválido (dueno | coordinador | asesor)")


@router.get("")
def listar(db: Session = Depends(get_db), user: User = Depends(_ver)):
    return [_salida(u) for u in db.query(User).order_by(User.id.asc()).all()]


@router.post("")
def crear(data: UsuarioIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    _valida_rol(data.rol)
    if len(data.password) < 6:
        raise HTTPException(422, "La contraseña debe tener al menos 6 caracteres")
    email = data.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "Ya existe un usuario con ese correo")
    secciones = permisos.limpiar_secciones(
        data.secciones if data.secciones is not None else permisos.DEFAULT_SECCIONES.get(data.rol))
    u = User(email=email, nombre=data.nombre, rol=data.rol, secciones=secciones,
             asesor_id=data.asesor_id, activo=True,
             password_hash=auth.hash_password(data.password))
    db.add(u)
    db.commit()
    return _salida(u)


@router.put("/{user_id}/acceso")
def cambiar_acceso(user_id: int, data: AccesoIn, db: Session = Depends(get_db),
                   user: User = Depends(auth.solo_dueno)):
    _valida_rol(data.rol)
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    u.rol = data.rol
    u.secciones = permisos.limpiar_secciones(
        data.secciones if data.secciones is not None else permisos.DEFAULT_SECCIONES.get(data.rol))
    u.asesor_id = data.asesor_id
    db.commit()
    return _salida(u)


@router.post("/{user_id}/toggle")
def toggle_activo(user_id: int, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    if user_id == user.id:
        raise HTTPException(400, "No puedes desactivarte a ti mismo")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    u.activo = not u.activo
    db.commit()
    return _salida(u)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, data: PasswordIn, db: Session = Depends(get_db),
                   user: User = Depends(auth.solo_dueno)):
    if len(data.password) < 6:
        raise HTTPException(422, "La contraseña debe tener al menos 6 caracteres")
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    u.password_hash = auth.hash_password(data.password)
    db.commit()
    return {"ok": True}
