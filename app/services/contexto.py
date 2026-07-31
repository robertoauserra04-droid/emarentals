"""Contexto vivo del bot: el equipo lo escribe en el panel, el bot lo lee en cada turno.

`grounding_activo(db)` arma el bloque de texto con todos los contextos activos, que el handler
pasa a `build_system_prompt(grounding=...)`. Así, en cuanto se sube contexto, el bot lo usa.
"""
from sqlalchemy.orm import Session

from app.models.lead import ContextoBot


def grounding_activo(db: Session) -> str:
    filas = (db.query(ContextoBot)
             .filter(ContextoBot.activo == True)  # noqa: E712
             .order_by(ContextoBot.id.asc()).all())
    if not filas:
        return ""
    bloques = [f"- {c.titulo.strip()}: {c.contenido.strip()}" for c in filas if c.contenido]
    if not bloques:
        return ""
    return ("CONTEXTO ADICIONAL DEL NEGOCIO (información que te dio el equipo; úsala para "
            "responder con precisión, sin inventar):\n" + "\n".join(bloques))
