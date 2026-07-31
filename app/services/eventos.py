"""Registro de eventos del lead (bitácora). Se llama desde los puntos clave del flujo."""
from sqlalchemy.orm import Session

from app.models.lead_evento import LeadEvento


def registrar(db: Session, lead_id: int, tipo: str, detalle: str, autor: str = "sistema") -> None:
    """Agrega un evento a la bitácora del lead. NO hace commit (lo hace el caller)."""
    db.add(LeadEvento(lead_id=lead_id, tipo=tipo, detalle=detalle, autor=autor))
