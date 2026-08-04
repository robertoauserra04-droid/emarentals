"""Contexto vivo del bot: el equipo lo escribe en el panel, el bot lo lee en cada turno.

`grounding_activo(db)` devuelve DOS bloques que el prompt coloca en sitios distintos:

  - **dato**  → información del negocio (precios, cobertura, plazos). Va arriba, junto a "Sobre
    nosotros". Sirve para que el bot SEPA cosas.
  - **regla** → cómo debe comportarse. Va junto a las reglas duras, encabezado como obligatorio.
    Sirve para que el bot HAGA cosas.

Antes todo iba en un solo bloque etiquetado "información del negocio", al final del prompt y
después de "PROHIBIDO (reglas duras)". Por eso lo que el equipo escribía sobre comportamiento no
se cumplía: se le estaba pidiendo al modelo que lo tratara como dato de consulta.

Lo que NO se resuelve aquí: mensajes que dependen de en qué fase cayó el lead. Eso necesita ser
determinista y vive en el `mensaje_cierre` de cada fase, enviado por código.
"""
from sqlalchemy.orm import Session

from app.models.lead import ContextoBot

TIPOS = ("dato", "regla")


def grounding_activo(db: Session) -> tuple[str, str]:
    """Devuelve (bloque_datos, bloque_reglas). Cada uno vacío si no hay contexto de ese tipo."""
    filas = (db.query(ContextoBot)
             .filter(ContextoBot.activo == True)  # noqa: E712
             .order_by(ContextoBot.id.asc()).all())

    datos: list[str] = []
    reglas: list[str] = []
    for c in filas:
        if not c.contenido:
            continue
        linea = f"- {c.titulo.strip()}: {c.contenido.strip()}"
        # Las filas viejas no tienen `tipo` (quedan en NULL tras el ADD COLUMN) → cuentan como dato.
        if (c.tipo or "dato") == "regla":
            reglas.append(linea)
        else:
            datos.append(linea)

    bloque_datos = ""
    if datos:
        bloque_datos = ("INFORMACIÓN DEL NEGOCIO (te la dio el equipo; úsala para responder con "
                        "precisión, sin inventar):\n" + "\n".join(datos))

    bloque_reglas = ""
    if reglas:
        bloque_reglas = ("REGLAS DEL EQUIPO — OBLIGATORIAS. Las escribió el equipo de EMA y tienen "
                         "prioridad sobre tus respuestas genéricas. Cúmplelas al pie de la letra:\n"
                         + "\n".join(reglas))

    return bloque_datos, bloque_reglas
