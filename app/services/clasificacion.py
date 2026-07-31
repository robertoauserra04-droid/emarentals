"""Perfil estratégico del lead — cuadrante 2×2 (Ticket × Uso) + horas estimadas.

Se calcula solo (determinista) al guardar. Dos variables clave:
  - ticket_mensual vs umbral (default $20,000 MXN/mes, configurable)
  - uso: 'reventa' (pone los muebles frente a sus propios clientes/unidades) | 'propio' (uso propio)

            │  Bajo ticket        │  Alto ticket
  reventa   │  Aliado Operativo   │  Socio Estratégico
  propio    │  Cliente Estándar   │  Cliente Premium

3 variables opcionales ajustan horas/semana sobre una base por perfil.
"""
from sqlalchemy.orm import Session

from app.models.lead import AppSetting

UMBRAL_DEFAULT = 20000.0

PERFILES = {
    "socio_estrategico": {"label": "Socio Estratégico", "emoji": "🟢", "color": "#3f7d55"},
    "aliado_operativo":  {"label": "Aliado Operativo",  "emoji": "🔵", "color": "#6b8ba4"},
    "cliente_premium":   {"label": "Cliente Premium",   "emoji": "🟡", "color": "#c8a13e"},
    "cliente_estandar":  {"label": "Cliente Estándar",  "emoji": "⚪", "color": "#9a9188"},
    "sin_clasificar":    {"label": "Sin clasificar",    "emoji": "⬜", "color": "#c3bcb3"},
}

# Horas base por perfil (ajustables). El potencial de escala y la urgencia suman encima.
BASE_HORAS = {
    "socio_estrategico": 5,
    "aliado_operativo":  3,
    "cliente_premium":   2,
    "cliente_estandar":  1,
}
ESCALA_BONUS = {"1-3": 0, "4-6": 1, "7-15": 2, "+15": 3}


def get_umbral(db: Session) -> float:
    s = db.query(AppSetting).filter(AppSetting.key == "ticket_umbral").first()
    try:
        return float(s.value) if s and s.value else UMBRAL_DEFAULT
    except (TypeError, ValueError):
        return UMBRAL_DEFAULT


def set_umbral(db: Session, valor: float) -> None:
    s = db.query(AppSetting).filter(AppSetting.key == "ticket_umbral").first()
    if s:
        s.value = str(valor)
    else:
        db.add(AppSetting(key="ticket_umbral", value=str(valor)))
    db.commit()


def compute_perfil(lead, umbral: float) -> str:
    """Cuadrante 2×2. 'sin_clasificar' si faltan las 2 variables clave (ticket + uso)."""
    if lead.ticket_mensual is None or not lead.uso:
        return "sin_clasificar"
    alto = float(lead.ticket_mensual) >= umbral
    reventa = lead.uso == "reventa"
    if reventa:
        return "socio_estrategico" if alto else "aliado_operativo"
    return "cliente_premium" if alto else "cliente_estandar"


def compute_horas(lead, perfil: str) -> int:
    """Base por perfil + potencial de escala + urgencia de cierre."""
    if perfil == "sin_clasificar":
        return 0
    h = BASE_HORAS.get(perfil, 1)
    h += ESCALA_BONUS.get(lead.potencial_escala or "1-3", 0)
    if (lead.urgencia_cierre or "") == "ya":
        h += 1
    return h


def clasificar(db: Session, lead) -> tuple[str, int]:
    """Recalcula y GUARDA perfil + horas en el lead. Devuelve (perfil, horas)."""
    umbral = get_umbral(db)
    perfil = compute_perfil(lead, umbral)
    horas = compute_horas(lead, perfil)
    lead.perfil = perfil
    lead.horas_estimadas = horas
    return perfil, horas
