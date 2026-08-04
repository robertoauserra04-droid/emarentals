"""Recuperación automática de leads — re-engagement con plantillas de WhatsApp.

Reuso del motor de vella panel/Bell, adaptado. Un lead que mostró interés y dejó de
responder recibe hasta MAX_ATTEMPTS plantillas (aprobadas por Meta, obligatorias fuera de la
ventana de 24h) en cadencia configurable (default 2/5/12 días de silencio). La secuencia se
cancela si el lead responde (webhook → register_inbound) y se corta para siempre con opt-out.

`run_recovery_tick()` lo llama el scheduler de `main.py` cada 30 min.

Idempotencia dura: cada intento inserta un RecoveryEvent 'pending' cuyo unique
(lead_id, cycle, attempt_no) se commitea ANTES de llamar a Kapso — un proceso concurrente
falla en el INSERT y nunca envía doble.

⚠️ ARRANCAR LIMPIO: encender `recovery_enabled=true` en un tenant con RecoveryEvent viejos
puede duplicar. En un cliente nuevo esto no aplica; en una migración, limpiar primero.
"""
import logging
import zoneinfo
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.lead import AppSetting, EmaLead, RecoveryEvent
from app.services import kapso

logger = logging.getLogger(__name__)

TZ_MX = zoneinfo.ZoneInfo("America/Mexico_City")
MAX_ATTEMPTS = 3
LIFETIME_CAP = 6            # envíos reales máximos por lead, contando todos los ciclos
SEND_HOUR_START = 10       # ventana de envío (hora de México), inclusive
SEND_HOUR_END = 20         # exclusiva: 10:00 a 19:59

DEFAULTS = {
    "recovery_enabled": "false",     # cámbialo a "true" para encender el auto
    "recovery_dry_run": "true",      # "false" para enviar de verdad
    "recovery_daily_limit": "15",
    "recovery_delays_days": "2,5,12",
    "recovery_min_score": "0",
}

# Plantillas de la agencia, en orden. DEBEN estar aprobadas en Kapso (0 variables).
SECUENCIA_TEMPLATES = ["inmo_recuperacion_1", "inmo_recuperacion_2", "inmo_recuperacion_3"]

_OPTOUT_KEYWORDS = (
    "no me interesa", "no gracias", "ya no me interesa", "ya no quiero",
    "deja de escribir", "deja de mandar", "no me escribas", "no molestar",
    "quiero darme de baja", "dar de baja", "baja por favor", "stop",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _get(db: Session, key: str) -> str:
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    return s.value if s else DEFAULTS.get(key, "")


def is_optout(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _OPTOUT_KEYWORDS)


def register_inbound(db: Session, lead: EmaLead, text: str) -> None:
    """Al llegar un inbound: si es opt-out, corta para siempre; si no, cancela la secuencia
    activa (subiendo el ciclo) para reengancharlo limpio si vuelve a callar."""
    if is_optout(text):
        lead.opted_out = True
        lead.next_recovery_at = None
        return
    if lead.recovery_attempts and lead.recovery_attempts > 0:
        lead.recovery_cycle = (lead.recovery_cycle or 0) + 1
        lead.recovery_attempts = 0
    lead.next_recovery_at = None


def _delays(db: Session) -> list[int]:
    raw = _get(db, "recovery_delays_days") or "2,5,12"
    try:
        return [int(x) for x in raw.split(",")]
    except ValueError:
        return [2, 5, 12]


def _envios_totales(db: Session, lead: EmaLead) -> int:
    return (db.query(RecoveryEvent)
            .filter(RecoveryEvent.lead_id == lead.id,
                    RecoveryEvent.status.in_(("sent", "dry_run")))
            .count())


def _elegibles(db: Session):
    """Leads recuperables: los que están en una fase con el toggle de Recuperación encendido.

    Antes esto era una tupla fija `("interesado", "calificado", "asignado")` que además había
    quedado desincronizada: `calificado` y `asignado` ya no existen en el pipeline, así que solo
    `interesado` hacía match. Ahora lo decide el admin desde la pantalla de Fases.
    """
    from app.routers.fases import claves_con_recuperacion

    claves = claves_con_recuperacion(db)
    if not claves:
        return []
    return (db.query(EmaLead)
            .filter(EmaLead.opted_out.is_(False),
                    EmaLead.recovery_paused.is_(False),
                    EmaLead.estado.in_(claves),
                    EmaLead.es_venta.is_(False))
            .all())


def _debe_enviar(db: Session, lead: EmaLead, delays: list[int]) -> bool:
    attempts = lead.recovery_attempts or 0
    if attempts >= MAX_ATTEMPTS:
        return False
    if _envios_totales(db, lead) >= LIFETIME_CAP:
        return False
    ultima = _aware(lead.last_message_at) or _aware(lead.last_recovery_at)
    if ultima is None:
        return False
    dias_silencio = (_now() - ultima).total_seconds() / 86400.0
    return dias_silencio >= delays[min(attempts, len(delays) - 1)]


def _enviar(db: Session, lead: EmaLead, dry_run: bool) -> bool:
    """Inserta el RecoveryEvent (idempotente) y manda la plantilla. Devuelve True si envió."""
    attempt_no = (lead.recovery_attempts or 0) + 1
    template = SECUENCIA_TEMPLATES[min(attempt_no - 1, len(SECUENCIA_TEMPLATES) - 1)]
    ev = RecoveryEvent(lead_id=lead.id, phone=lead.phone, cycle=lead.recovery_cycle or 0,
                       attempt_no=attempt_no, template_name=template,
                       status="dry_run" if dry_run else "pending", via="auto")
    db.add(ev)
    try:
        db.commit()   # el unique se commitea ANTES de llamar a Kapso (anti-doble)
    except IntegrityError:
        db.rollback()
        logger.warning("[recovery] intento duplicado ignorado lead=%s attempt=%s", lead.id, attempt_no)
        return False

    if not dry_run:
        try:
            kapso.send_template_sync(lead.phone, template)
            ev.status = "sent"
            ev.sent_at = _now()
        except Exception as e:  # noqa: BLE001
            ev.status = "failed"
            ev.error = str(e)[:500]
            db.commit()
            logger.error("[recovery] fallo enviando a %s: %s", lead.phone, e)
            return False

    lead.recovery_attempts = attempt_no
    lead.last_recovery_at = _now()
    db.commit()
    logger.warning("[recovery] %s intento %s (%s) a %s", "DRY" if dry_run else "SENT",
                   attempt_no, template, lead.phone)
    return True


def run_recovery_tick() -> dict:
    """Un tick del scheduler. Devuelve un resumen {revisados, enviados}."""
    db = SessionLocal()
    try:
        if _get(db, "recovery_enabled") != "true":
            return {"enabled": False, "revisados": 0, "enviados": 0}

        # Ventana horaria (hora de México).
        hora = datetime.now(TZ_MX).hour
        if not (SEND_HOUR_START <= hora < SEND_HOUR_END):
            return {"enabled": True, "revisados": 0, "enviados": 0, "fuera_de_ventana": True}

        dry_run = _get(db, "recovery_dry_run") == "true"
        try:
            limite_diario = int(_get(db, "recovery_daily_limit") or "15")
        except ValueError:
            limite_diario = 15
        delays = _delays(db)

        enviados = 0
        revisados = 0
        for lead in _elegibles(db):
            revisados += 1
            if enviados >= limite_diario:
                break
            if _debe_enviar(db, lead, delays):
                if _enviar(db, lead, dry_run):
                    enviados += 1
        logger.warning("[recovery] tick: revisados=%s enviados=%s dry=%s", revisados, enviados, dry_run)
        return {"enabled": True, "revisados": revisados, "enviados": enviados, "dry_run": dry_run}
    finally:
        db.close()


def seed_defaults(db: Session) -> None:
    """Crea los AppSetting de recovery si faltan (idempotente)."""
    for k, v in DEFAULTS.items():
        if db.query(AppSetting).filter(AppSetting.key == k).first() is None:
            db.add(AppSetting(key=k, value=v))
    db.commit()
