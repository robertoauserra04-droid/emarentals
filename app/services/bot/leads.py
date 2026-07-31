"""CRUD de EmaLead + la GUARDA DE ETAPA determinista ("buen lead").

La IA propone la clasificación con `capturar_lead`; aquí el CÓDIGO valida el salto a
'calificado' antes de persistir, para que un bot agradable NO sobrecalifique al curioso. Ese es
el "casco" que distingue este proyecto de Bell crudo (patrón G2 + cinturón de robustez).

Criterio de BUEN LEAD (definido con el cliente): buen ticket final + quiere mucho (volumen) +
quiere a futuro (plazo largo). Los pesos/umbrales viven aquí, fáciles de afinar.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.lead import EmaLead, ESTADOS_VALIDOS

# ─────────── Umbrales afinables del "buen lead" ───────────
SEGMENTOS_TICKET_ALTO = {"airbnb", "corporativo"}         # ticket alto por segmento
NECESIDAD_VOLUMEN_ALTO = {"paquete", "casa_completa", "oficina_completa"}  # "quiere mucho"
PLAZO_LARGO_MESES = 12                                      # "quiere a futuro"


def norm_phone(phone: str) -> str:
    """52XXXXXXXXXX — quita el + y el 1 de móvil que México agrega en WA (521...).
    Para IG/Messenger el identificador es un PSID; se deja tal cual (no empieza en 521)."""
    d = (phone or "").lstrip("+")
    if len(d) == 13 and d.startswith("521"):
        d = "52" + d[3:]
    return d


def _nombre_generico(nombre: str | None, phone: str) -> bool:
    """True si el nombre guardado NO es real (vacío / 'cliente' / el propio teléfono)."""
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


def _es_buen_lead(lead: EmaLead, args: dict) -> bool:
    """Señal de BUEN LEAD: ticket alto (segmento o volumen) O plazo largo ("a futuro")."""
    segmento = args.get("segmento") or lead.segmento
    necesidad = args.get("necesidad") or lead.necesidad
    plazo = args.get("plazo_meses") or lead.plazo_meses
    ticket_ok = segmento in SEGMENTOS_TICKET_ALTO
    volumen_ok = necesidad in NECESIDAD_VOLUMEN_ALTO
    futuro_ok = bool(plazo and int(plazo) >= PLAZO_LARGO_MESES)
    return ticket_ok or volumen_ok or futuro_ok


def validar_etapa(lead: EmaLead, estado_propuesto: str | None, args: dict) -> str | None:
    """GUARDA DE ETAPA (determinista). Para marcar 'calificado' (buen lead → alerta al admin)
    se exigen (1) mínimos reales para no alertar a un curioso: necesidad + plazo + (fecha o zona),
    y (2) señal de buen lead: ticket/volumen/plazo largo. Si no, se degrada a 'interesado'.
    """
    if estado_propuesto not in ESTADOS_VALIDOS:
        return None  # inválido: no tocar el estado actual
    if estado_propuesto == "calificado":
        necesidad = args.get("necesidad") or lead.necesidad
        plazo = args.get("plazo_meses") or lead.plazo_meses
        fecha = args.get("fecha_entrega") or lead.fecha_entrega
        zona = args.get("zona") or lead.zona
        base_ok = bool(necesidad and plazo and (fecha or zona))
        if not (base_ok and _es_buen_lead(lead, args)):
            return "interesado"   # tibio: NO dispara alerta
    return estado_propuesto


# Piso de score por fase: hace visible que el prospecto "sube" al avanzar de etapa.
_PISO_POR_ESTADO = {"interesado": 35, "calificado": 65, "asignado": 70, "ganado": 100}


def recompute_score_calif(lead: EmaLead) -> int:
    """Score comercial determinista 0-100, reponderado a los 3 ejes del buen lead:
    ticket (segmento) + volumen (necesidad) + futuro (plazo) + urgencia (fecha) + zona."""
    s = 0
    s += {"corporativo": 30, "airbnb": 25, "oficina": 15, "residencial": 10}.get(lead.segmento or "", 0)
    s += {"casa_completa": 25, "oficina_completa": 25, "paquete": 15,
          "pieza_suelta": 5}.get(lead.necesidad or "", 0)
    plazo = lead.plazo_meses or 0
    if plazo >= 12:
        s += 20
    elif plazo >= 6:
        s += 12
    elif plazo >= 3:
        s += 5
    s += {"ya": 15, "1-4sem": 10, ">1mes": 3}.get(lead.fecha_entrega or "", 0)
    if lead.zona:
        s += 5
    s = min(100, s)
    if lead.estado != "perdido":
        s = max(s, _PISO_POR_ESTADO.get(lead.estado or "", 0))
    lead.score_calif = s
    return s


def apply_capturar_lead(lead: EmaLead, args: dict) -> str:
    """Aplica la tool capturar_lead con semántica COALESCE (vacío NO sobreescribe) + la
    guarda de etapa. Devuelve un texto corto de resultado para la IA."""
    if args.get("nombre") and not lead.name:
        lead.name = args["nombre"].strip()

    for campo in ("segmento", "necesidad", "fecha_entrega", "zona", "presupuesto",
                  "nivel_interes", "que_pregunto", "resumen", "motivo_perdida"):
        val = args.get(campo)
        if val not in (None, ""):
            setattr(lead, campo, val)
    if args.get("plazo_meses") not in (None, ""):
        try:
            lead.plazo_meses = int(args["plazo_meses"])
        except (TypeError, ValueError):
            pass

    # Estado: pasa por la guarda determinista.
    estado_validado = validar_etapa(lead, args.get("estado"), args)
    if estado_validado:
        lead.estado = estado_validado

    # Auto-avance suave: si mostró interés real pero seguía en 'nuevo', pásalo a 'interesado'.
    if lead.estado in (None, "nuevo") and lead.nivel_interes in ("Alto", "Medio"):
        lead.estado = "interesado"

    recompute_score_calif(lead)
    return "datos del prospecto actualizados"


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
