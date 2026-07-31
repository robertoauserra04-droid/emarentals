"""Orquestador del bot: handle_inbound(). Clon adaptado de Bell (vella panel), despojado.

Flujo: registrar lead SIEMPRE → decidir si el bot responde (toggles = coexistencia) → construir
historial + prompt → generate_reply con tools (capturar_lead / alertar_asesor) → fact_guard → si
el lead quedó CALIFICADO (buen lead), alertar al admin UNA vez y ceder al humano → burbujas → enviar.

EMA Rentals NO agenda, NO cotiza, NO maneja propiedades. Solo filtra leads y alerta.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.ema_config import BOT
from app.models.lead import AppSetting, EmaLead
from app.models.messaging import Conversation
from app.services import notificaciones
from app.services.bot import ai, leads
from app.services.bot.guards import fact_guard
from app.services.bot.prompt import build_system_prompt
from app.models.messaging import ChatMessage, MessageDirection

logger = logging.getLogger(__name__)

MAX_BUBBLES = 2


def _owner_phones() -> set[str]:
    return {p.strip() for p in (settings.owner_phones or "").split(",") if p.strip()}


def _get_setting(db: Session, key: str) -> str | None:
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    return s.value if s else None


def _bot_global_enabled(db: Session) -> bool:
    return _get_setting(db, "bot_enabled") != "false"


def _build_history(db: Session, phone: str, current_text: str) -> list[dict]:
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.phone == phone)
        .order_by(ChatMessage.created_at.desc())
        .limit(19)
        .all()
    )
    history = [
        {"role": "user" if m.direction == MessageDirection.inbound else "assistant",
         "content": m.body}
        for m in reversed(msgs)
    ]
    if not history or history[-1]["content"] != current_text:
        history.append({"role": "user", "content": current_text})
    return history


def _split_bubbles(reply: str) -> list[str]:
    parts = [p.strip() for p in reply.split("\n\n") if p.strip()]
    if len(parts) <= MAX_BUBBLES:
        return parts or [reply]
    return parts[: MAX_BUBBLES - 1] + ["\n\n".join(parts[MAX_BUBBLES - 1:])]


def handle_inbound(db: Session, phone: str, text: str, name: str | None = None,
                   channel: str = "whatsapp", campania_id: int | None = None) -> None:
    logger.warning("[bot] handle_inbound phone=%s name=%s channel=%s", phone, name, channel)

    # Registrar/actualizar el lead SIEMPRE (entra al kanban aunque el bot no responda).
    lead = leads.get_or_create_lead(db, phone, name=name, channel=channel)
    leads.touch(lead)
    db.commit()

    # -------- ¿Responde el bot? (el lead ya quedó guardado) --------
    if not settings.openai_api_key:
        logger.warning("[bot] sin OPENAI_API_KEY, saliendo")
        return
    if not _bot_global_enabled(db):
        logger.warning("[bot] bot global desactivado, saliendo")
        return

    # Coexistencia: si un asesor tomó la conversación (bot_active=False), el bot calla.
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    if conv is not None and not conv.bot_active:
        logger.warning("[bot] conversación %s con bot apagado (humano al mando), saliendo", phone)
        return
    if not lead.bot_active:
        logger.warning("[bot] bot inactivo para %s, saliendo", phone)
        return

    history = _build_history(db, phone, text)
    es_primer_contacto = lead.message_count == 1
    # Contexto vivo que el equipo subió desde el panel (se lee en cada turno).
    from app.services import contexto
    grounding = contexto.grounding_activo(db)
    system = build_system_prompt(lead, es_primer_contacto=es_primer_contacto, grounding=grounding)

    actions = {"alertar": False, "motivo": ""}

    def _on_capturar_lead(args: dict) -> str:
        return leads.apply_capturar_lead(lead, args)

    def _on_alertar_asesor(args: dict) -> str:
        actions["alertar"] = True
        actions["motivo"] = args.get("motivo", "")
        return "Listo: se avisará a un asesor. Despídete formal y breve."

    try:
        reply = ai.generate_reply(
            system, history,
            handlers={
                "capturar_lead": _on_capturar_lead,
                "alertar_asesor": _on_alertar_asesor,
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[bot] error de IA: %s", e)
        return

    if not reply:
        reply = f"Con gusto le ayudo. Soy {BOT['nombre']} de EMA Rentals. ¿En qué puedo apoyarle?"

    # Guarda dura anti-cifra: ningún monto que no venga del prompt o del historial.
    sources = [system] + [m["content"] or "" for m in history]
    reply, blocked = fact_guard(reply, *sources)
    if blocked:
        logger.warning("[bot] fact_guard bloqueó una cifra inventada para %s", phone)

    # BUEN LEAD: el bot pidió asesor, o la guarda determinista lo marcó 'calificado'.
    if actions["alertar"] or lead.estado == "calificado":
        lead.escalated = True
        if lead.estado in (None, "nuevo", "interesado"):
            lead.estado = "calificado"
        _alertar_una_vez(db, lead)
        # "Califica y entrega": el bot cede la conversación al asesor humano.
        lead.bot_active = False
        if conv is not None:
            conv.bot_active = False

    db.commit()

    # Enviar la respuesta (en burbujas).
    try:
        from app.services import messaging_out
        for bubble in _split_bubbles(reply):
            messaging_out.send_text(db, phone, bubble, channel=channel, name=name)
        logger.warning("[bot] respuesta enviada a %s (msg #%s)", phone, lead.message_count)
    except Exception as e:  # noqa: BLE001
        logger.error("[bot] error enviando mensaje: %s", e)


def _alertar_una_vez(db: Session, lead: EmaLead) -> None:
    """Dispara la alerta al admin SOLO la primera vez que el lead califica (idempotente)."""
    if lead.alertado_at is not None:
        return
    try:
        notificaciones.alertar_admin(lead)
        lead.alertado_at = datetime.now(timezone.utc)
        logger.warning("[bot] alerta de buen lead enviada al admin: %s (score %s)",
                       lead.phone, lead.score_calif)
    except Exception as e:  # noqa: BLE001
        logger.error("[bot] no se pudo alertar al admin: %s", e)
