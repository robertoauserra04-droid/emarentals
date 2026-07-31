"""Guardas DETERMINISTAS del bot (el código decide, no el LLM).

`fact_guard`: compuerta dura anti-alucinación de cifras (port directo de vella panel/Bell,
que a su vez viene de colegiolizardi). Bell redacta libre, pero si afirma un monto/porcentaje
que NO está en la fuente de verdad (system prompt + historial), la cifra es inventada → se
sustituye por una deflexión que remite al asesor (en inmobiliaria NO hay un precio único).

Alcance CONSERVADOR a propósito (dinero / % / miles) para no estorbar la conversación: los
números chicos de m², recámaras o baños no disparan.
"""
from __future__ import annotations

import re

from app.ema_config import EMPRESA

_FIGURE_RE = re.compile(
    r"\$\s?\d[\d.,]*"
    r"|\b\d[\d.,]*\s?(?:pesos?|mxn|mil|millones?|mdp)\b"
    r"|\b\d+(?:[.,]\d+)?\s?%"
    r"|\b\d{1,3}(?:[.,]\d{3})+\b",
    re.IGNORECASE,
)


def deflexion_precio() -> str:
    return EMPRESA["deflexion_precio"]


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def fact_guard(draft: str, *sources: str) -> tuple[str, bool]:
    """Devuelve `(texto, bloqueado)`. Si una cifra de dinero/% del borrador no está
    respaldada en `sources` (system prompt + historial), devuelve la deflexión."""
    if not draft or not draft.strip():
        return draft, False
    source_digits = _digits(" ".join(s for s in sources if s))
    for m in _FIGURE_RE.findall(draft):
        d = _digits(m)
        if len(d) < 2:
            continue
        if d not in source_digits:
            return deflexion_precio(), True
    return draft, False
