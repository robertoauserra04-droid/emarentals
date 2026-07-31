"""Config del motor de recuperación + disparo manual del tick (solo dueño)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import AppSetting
from app.models.user import User
from app.services import auth, recovery

router = APIRouter(prefix="/api/recovery", tags=["recovery"])

_CLAVES = ["recovery_enabled", "recovery_dry_run", "recovery_daily_limit",
           "recovery_delays_days", "recovery_min_score"]


class SettingIn(BaseModel):
    key: str
    value: str


@router.get("/config")
def config(db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    out = {}
    for k in _CLAVES:
        s = db.query(AppSetting).filter(AppSetting.key == k).first()
        out[k] = s.value if s else recovery.DEFAULTS.get(k, "")
    return out


@router.put("/config")
def set_config(data: SettingIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    if data.key not in _CLAVES:
        return {"ok": False, "error": "clave no permitida"}
    s = db.query(AppSetting).filter(AppSetting.key == data.key).first()
    if s:
        s.value = data.value
    else:
        db.add(AppSetting(key=data.key, value=data.value))
    db.commit()
    return {"ok": True, data.key: data.value}


@router.post("/tick")
def tick(user: User = Depends(auth.solo_dueno)):
    """Corre un tick de recuperación ahora (para probar). Respeta enabled/dry_run/ventana."""
    return recovery.run_recovery_tick()
