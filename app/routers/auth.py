"""Login del panel (dueño / asesor)."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services import auth, permisos

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username).first()
    if not user or not auth.verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos")
    if not user.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario desactivado")
    return {"access_token": auth.crear_token(user), "token_type": "bearer",
            "rol": user.rol, "nombre": user.nombre, "secciones": permisos.secciones_de(user)}


@router.get("/me")
def me(user: User = Depends(auth.current_user)):
    return {"id": user.id, "email": user.email, "nombre": user.nombre,
            "rol": user.rol, "asesor_id": user.asesor_id,
            "secciones": permisos.secciones_de(user)}


@router.get("/secciones")
def catalogo_secciones(user: User = Depends(auth.current_user)):
    """Catálogo de secciones (para pintar los checkboxes en la pantalla de Usuarios)."""
    return [{"clave": c, "etiqueta": e} for c, e in permisos.SECCIONES]
