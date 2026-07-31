"""Auth: hash de contraseña + JWT + dependencias de rol (dueño / asesor)."""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User

# pbkdf2_sha256: puro Python (hashlib), sin dependencia nativa de bcrypt ni el bug de
# passlib+bcrypt 4.x (autotest de 72 bytes). Seguro y portable en Railway.
_pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(p: str) -> str:
    return _pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    return _pwd.verify(p, h)


def crear_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": str(user.id), "rol": user.rol, "asesor_id": user.asesor_id, "exp": exp}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def current_user(token: str = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    cred = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas")
    try:
        data = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        uid = int(data.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise cred
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise cred
    return user


def solo_dueno(user: User = Depends(current_user)) -> User:
    if user.rol != "dueno":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo el coordinador puede ver esto")
    return user


def requiere_seccion(seccion: str):
    """Fábrica de dependencia: exige que el usuario tenga acceso a `seccion` (dueño = todo)."""
    from app.services import permisos

    def _dep(user: User = Depends(current_user)) -> User:
        if not user.activo:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario desactivado")
        if not permisos.puede(user, seccion):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Sin acceso a la sección '{seccion}'")
        return user

    return _dep
