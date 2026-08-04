"""Qué leads se ven en el tablero. Punto ÚNICO del filtro.

Hasta ahora NINGUNA vista filtraba nada: Kanban, Bandeja, Métricas y Resumen traían todos los
leads de la tabla. Se quedan fuera los **no-leads** (`ContactoNoLead`): teléfonos que no son
prospectos (proveedores, el número del dueño, spam). La marca es por teléfono y permanente, y
vive en su propia tabla: por eso sobrevive a que el lead se borre.

(Antes había un segundo caso, los leads `oculto`, que se archivaban en la sección Historial. Esa
pantalla se eliminó: hoy sacar del tablero borra de verdad — ver `app/services/purga.py`.)

**Teléfonos:** los leads se guardan como los manda el canal (WhatsApp MX trae el 1 de móvil:
`5218110000030`), mientras que `ContactoNoLead` guarda el normalizado (`528110000030`). Comparar
crudo contra normalizado hacía que marcar "no es lead" a un número mexicano no sirviera de nada:
el lead se quedaba en el tablero y el bot le seguía contestando. Por eso todo aquí compara por
VARIANTES: el normalizado y el que trae el 1.
"""
from sqlalchemy.orm import Session

from app.models.lead import ContactoNoLead, EmaLead
from app.services.bot.leads import norm_phone


def variantes(telefono: str) -> set[str]:
    """`{52XXXXXXXXXX, 521XXXXXXXXXX}` para un número mexicano; el mismo string para todo lo demás
    (los PSID de Instagram/Messenger no son teléfonos y `norm_phone` los deja intactos)."""
    tel = norm_phone(telefono or "")
    if not tel:
        return set()
    out = {tel}
    if len(tel) == 12 and tel.startswith("52"):
        out.add("521" + tel[2:])
    return out


def telefonos_no_lead(db: Session) -> set[str]:
    """Teléfonos marcados 'no es lead', con sus variantes, para comparar contra el `phone` crudo
    que guardan `EmaLead` y `Conversation`."""
    fuera: set[str] = set()
    for c in db.query(ContactoNoLead).all():
        fuera |= variantes(c.telefono)
    return fuera


def visibles(q):
    """Aplica el filtro a un query de EmaLead."""
    return q.filter(EmaLead.phone.notin_(telefonos_no_lead(q.session)))


def leads_visibles(db: Session):
    return visibles(db.query(EmaLead))


def es_no_lead(db: Session, phone: str) -> bool:
    """¿Este teléfono está marcado como 'no es lead'? Lo consulta el bot antes de contestar."""
    tel = norm_phone(phone or "")
    if not tel:
        return False
    return db.query(ContactoNoLead).filter(ContactoNoLead.telefono == tel).first() is not None
