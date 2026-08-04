"""Borrado definitivo de un lead. La ÚNICA excepción a la política de "sin DELETE físico".

Antes, quitar a alguien del tablero solo lo ocultaba (`EmaLead.oculto`) y quedaba archivado en la
sección Historial. Esa pantalla se eliminó: hoy sacar del tablero significa BORRAR, con advertencia
explícita en el panel. No hay papelera ni forma de deshacerlo.

Efecto buscado: si esa persona vuelve a escribir, `get_or_create_lead` la crea de cero en la fase
de entrada con el bot activo — se trata como nueva sin código extra. La única forma de que el bot
deje de contestarle es el botón "No lead", que marca el TELÉFONO en `ContactoNoLead` (tabla aparte,
por eso sobrevive al borrado) y además purga sus datos.
"""
import logging

from sqlalchemy.orm import Session

from app.models.lead import EmaLead, RecoveryEvent
from app.models.lead_evento import LeadEvento, LeadNota
from app.models.messaging import ChatMessage, Conversation

logger = logging.getLogger(__name__)


def borrar_lead(db: Session, lead: EmaLead) -> str:
    """Borra el lead y TODO su rastro. Devuelve el teléfono. NO hace commit (lo hace el caller).

    Orden por dependencia: lo que apunta a `ema_leads.id` primero (RecoveryEvent, LeadEvento,
    LeadNota), luego lo que va por teléfono (ChatMessage, Conversation) y al final el lead.
    """
    phone = lead.phone
    lead_id = lead.id
    for modelo in (RecoveryEvent, LeadEvento, LeadNota):
        db.query(modelo).filter(modelo.lead_id == lead_id).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.phone == phone).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.phone == phone).delete(synchronize_session=False)
    db.delete(lead)
    logger.warning("[purga] lead %s (%s) borrado con todo su rastro", lead_id, phone)
    return phone
