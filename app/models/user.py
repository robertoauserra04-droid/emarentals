"""Usuario del panel — control granular (patrón Regional México: rol + secciones).

Roles: `dueno` (comodín, ve TODO), `coordinador` (ve lo que le asignen en `secciones`),
`asesor` (además del scoping de datos: solo sus leads/visitas). La visibilidad fina la da
`secciones` (lista de claves del catálogo en app/services/permisos.py); el dueño la ignora.
"""
from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True)
    email         = Column(String, unique=True, index=True, nullable=False)
    nombre        = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    rol           = Column(String, default="asesor")   # "dueno" | "coordinador" | "asesor"
    secciones     = Column(JSON, nullable=True)         # ["leads","agenda",...] (dueno = todo)
    activo        = Column(Boolean, default=True)       # soft-disable (no login si False)
    asesor_id     = Column(Integer, nullable=True)      # si rol=asesor, liga a Asesor
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
