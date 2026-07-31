"""CRUD de EmaLead + la CALIFICACIÓN determinista (el bot filtra, el código decide).

Flujo (renta): tipo de propiedad → pregunta ligada → tiempo de renta. Reglas de BUEN PROSPECTO:
  - Casa: siempre buen prospecto (por el simple hecho de ser casa).
  - Departamento: buen prospecto si tiene 2 o más recámaras.
  - Oficina: buen prospecto si es de 100 m² o más, o 20 personas o más.
Etiqueta de tiempo:
  - Casa/Departamento con 12+ meses = prospecto fuerte.
  - Oficina 0-12 meses = Tipo 1; Oficina 12+ meses = Tipo 2.
El bot nunca cierra venta ni demo; al terminar de filtrar, escala a un asesor.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.lead import EmaLead


def norm_phone(phone: str) -> str:
    """52XXXXXXXXXX — quita el + y el 1 de móvil que México agrega en WA (521...).
    Para IG/Messenger el identificador es un PSID; se deja tal cual."""
    d = (phone or "").lstrip("+")
    if len(d) == 13 and d.startswith("521"):
        d = "52" + d[3:]
    return d


def _nombre_generico(nombre: str | None, phone: str) -> bool:
    if not nombre:
        return True
    n = nombre.strip()
    return n == "" or n.lower() == "cliente" or n == str(phone)


def get_or_create_lead(db: Session, phone: str, name: str | None = None,
                       channel: str = "whatsapp") -> EmaLead:
    name_real = name.strip() if (name and not _nombre_generico(name, phone)) else None
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    if not lead:
        lead = EmaLead(phone=phone, name=name_real, estado="nuevo", source=channel)
        db.add(lead)
        db.flush()
    elif name_real and _nombre_generico(lead.name, phone):
        lead.name = name_real
    return lead


def touch(lead: EmaLead) -> None:
    lead.message_count = (lead.message_count or 0) + 1
    lead.last_message_at = datetime.now(timezone.utc)


def _marca_de(tp: str | None) -> str | None:
    if tp == "oficina":
        return "office"
    if tp in ("casa", "departamento"):
        return "rentals"
    return None


def evaluar_prospecto(lead: EmaLead) -> tuple[bool, str | None]:
    """Devuelve (es_buen_prospecto, tipo_oficina). Reglas del cliente."""
    tp = lead.tipo_propiedad
    if tp == "casa":
        bueno = True
    elif tp == "departamento":
        bueno = (lead.recamaras or 0) >= 2
    elif tp == "oficina":
        bueno = (lead.oficina_m2 or 0) >= 100 or (lead.oficina_personas or 0) >= 20
    else:
        bueno = False
    tipo_of = None
    if tp == "oficina":
        tipo_of = "tipo2" if lead.tiempo_renta == "12+" else "tipo1"
    return bueno, tipo_of


def _completo(lead: EmaLead) -> bool:
    """¿Ya tenemos lo necesario para clasificar (tipo + su dato + tiempo)?"""
    tp = lead.tipo_propiedad
    if not tp or not lead.tiempo_renta:
        return False
    if tp == "casa":
        return True                                   # casa ya es buen prospecto
    if tp == "departamento":
        return lead.recamaras is not None
    if tp == "oficina":
        return lead.oficina_m2 is not None or lead.oficina_personas is not None
    return False


def recompute_score_calif(lead: EmaLead) -> int:
    """Score 0-100 visible en el Kanban, derivado del buen prospecto + tiempo."""
    bueno, _ = evaluar_prospecto(lead)
    s = 80 if bueno else 45
    if lead.tiempo_renta == "12+":
        s += 15
    elif lead.tiempo_renta == "6-12":
        s += 5
    if lead.estado in ("nuevo", None) and not lead.tipo_propiedad:
        s = 0
    lead.score_calif = min(100, s)
    return lead.score_calif


def _texto_prospecto(lead: EmaLead, bueno: bool, tipo_of: str | None) -> str:
    if lead.tipo_propiedad == "oficina" and tipo_of:
        t = "Tipo 1 (hasta 1 año)" if tipo_of == "tipo1" else "Tipo 2 (más de 1 año)"
        return f"oficina · {t}" + (" · buen prospecto" if bueno else "")
    return "buen prospecto" if bueno else "prospecto en evaluación"


def apply_capturar_lead(lead: EmaLead, args: dict) -> str:
    """Aplica la tool capturar_lead y CLASIFICA de forma determinista (el bot filtra)."""
    if args.get("nombre") and not lead.name:
        lead.name = args["nombre"].strip()

    # Enteros
    for campo in ("recamaras", "oficina_m2", "oficina_personas"):
        v = args.get(campo)
        if v not in (None, ""):
            try:
                setattr(lead, campo, int(v))
            except (TypeError, ValueError):
                pass
    # Strings (COALESCE: vacío no sobreescribe)
    for campo in ("tipo_propiedad", "tiempo_renta", "uso", "zona", "presupuesto",
                  "nivel_interes", "que_pregunto", "resumen", "motivo_perdida"):
        v = args.get(campo)
        if v not in (None, ""):
            setattr(lead, campo, v)

    # Derivados
    if lead.tipo_propiedad:
        lead.marca = _marca_de(lead.tipo_propiedad)
        if not lead.modelo:
            lead.modelo = "renta"
    bueno, tipo_of = evaluar_prospecto(lead)
    lead.es_buen_prospecto = bueno
    lead.tipo_oficina = tipo_of

    # Estado (el código decide; nunca degrada de asignado/ganado/perdido).
    if args.get("motivo_perdida"):
        lead.estado = "perdido"
    elif lead.estado not in ("asignado", "ganado", "perdido"):
        if _completo(lead):
            lead.estado = "calificado" if bueno else "interesado"
        elif lead.tipo_propiedad or lead.nivel_interes in ("Alto", "Medio"):
            if lead.estado in (None, "nuevo"):
                lead.estado = "interesado"

    recompute_score_calif(lead)
    return "prospecto actualizado: " + _texto_prospecto(lead, bueno, tipo_of)


def mark_won(db: Session, lead: EmaLead, monto: str | None = None) -> None:
    lead.es_venta = True
    lead.estado = "ganado"
    if monto:
        lead.monto_cierre = monto
    db.commit()


def mark_lost(db: Session, lead: EmaLead, motivo: str | None = None) -> None:
    lead.estado = "perdido"
    if motivo:
        lead.motivo_perdida = motivo
    db.commit()
