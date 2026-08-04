"""Qué leads se ven en el tablero. Punto ÚNICO del filtro.

Hasta ahora NINGUNA vista filtraba nada: Kanban, Bandeja, Métricas y Resumen traían todos los
leads de la tabla, incluidos los de demo y los de prueba del simulador. Se quedan fuera:

  - **ocultos** (`EmaLead.oculto`) — el asesor los quitó del tablero. Reversible desde Historial.
  - **no-leads** (`ContactoNoLead`) — teléfonos que no son prospectos (proveedores, el número del
    dueño, spam). Marcado por teléfono y permanente: sobrevive a que el lead se borre o reinicie.

Ninguno de los dos borra datos: la conversación se sigue guardando y todo se ve en Historial.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lead import ContactoNoLead, EmaLead


def _sub_no_lead():
    return select(ContactoNoLead.telefono)


def visibles(q):
    """Aplica el filtro a un query de EmaLead.

    `isnot(True)` y no `is_(False)`: tras un ADD COLUMN las filas viejas quedan en NULL, y
    `oculto == False` no las capturaría.
    """
    return q.filter(EmaLead.oculto.isnot(True), EmaLead.phone.notin_(_sub_no_lead()))


def leads_visibles(db: Session):
    return visibles(db.query(EmaLead))


def es_no_lead(db: Session, phone: str) -> bool:
    """¿Este teléfono está marcado como 'no es lead'? Lo consulta el bot antes de contestar."""
    return db.query(ContactoNoLead).filter(ContactoNoLead.telefono == phone).first() is not None


def telefonos_no_lead(db: Session) -> set[str]:
    return {c.telefono for c in db.query(ContactoNoLead).all()}
