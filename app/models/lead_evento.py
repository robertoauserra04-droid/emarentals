"""Bitácora del lead (eventos) + notas internas del asesor — 'qué pasó con cada lead'.

Port de bienesraicesEnrique (LeadEvent + LeadNote), adaptado a nuestro stack.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class LeadEvento(Base):
    """Un hecho en la vida del lead: etapa cambiada, asesor asignado, cierre, etc."""
    __tablename__ = "lead_eventos"

    id         = Column(Integer, primary_key=True)
    lead_id    = Column(Integer, ForeignKey("ema_leads.id"), index=True)
    tipo       = Column(String, nullable=False)   # etapa | asignacion | cierre | nota | visita | mensaje
    detalle    = Column(Text, nullable=True)       # texto legible del evento
    autor      = Column(String, nullable=True)      # "bot" | nombre del usuario
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class LeadNota(Base):
    """Nota interna que el asesor escribe sobre el lead."""
    __tablename__ = "lead_notas"

    id         = Column(Integer, primary_key=True)
    lead_id    = Column(Integer, ForeignKey("ema_leads.id"), index=True)
    autor      = Column(String, nullable=True)
    texto      = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
