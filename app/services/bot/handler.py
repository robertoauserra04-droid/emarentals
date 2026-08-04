"""Orquestador del bot: handle_inbound(). Clon adaptado de Bell (vella panel), despojado.

Flujo: registrar lead SIEMPRE → decidir si el bot responde (toggles = coexistencia) → construir
historial + prompt → generate_reply con tools (capturar_lead / alertar_asesor) → fact_guard → si
el CUESTIONARIO YA TERMINÓ (o el prospecto pidió un humano), ceder al asesor y alertar → burbujas.

Regla de oro: nada se clasifica, se notifica ni se apaga hasta que `cuestionario_completo()` sea
True. La única excepción es que el prospecto pida hablar con una persona.

EMA Rentals NO agenda, NO cotiza, NO maneja propiedades. Solo filtra leads y alerta.
"""
import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.ema_config import BOT
from app.models.lead import AppSetting
from app.models.messaging import Conversation
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
                   channel: str = "whatsapp", campania_id: int | None = None,
                   enviar: bool = True) -> None:
    """enviar=False → modo prueba: NO manda por WhatsApp/IG; solo guarda la respuesta en el panel."""
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

    # Contacto marcado como "no es lead" (proveedor, número del dueño, spam): el bot nunca le
    # contesta, ni aunque escriba de nuevo. El mensaje ya quedó guardado arriba.
    from app.services import visibilidad
    if visibilidad.es_no_lead(db, phone):
        logger.warning("[bot] %s está marcado como 'no es lead', saliendo", phone)
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
    datos, reglas = contexto.grounding_activo(db)
    system = build_system_prompt(lead, es_primer_contacto=es_primer_contacto,
                                 datos=datos, reglas=reglas)

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

    # ¿El bot ya terminó de preguntar? Hasta entonces NO se clasifica, NO se notifica y NO se
    # apaga el bot. Antes esto colgaba de `lead.es_buen_prospecto`, que se ponía en True con solo
    # saber el tipo de propiedad: por eso el bot cerraba y avisaba antes de la última pregunta.
    completo = leads.cuestionario_completo(lead)
    pidio_humano = actions["alertar"]      # el prospecto pidió una persona, precios, o se negó

    # La fase se resuelve ANTES de decidir la alerta: es la fase la que manda (Tanda 2).
    # Si el admin borró la fase destino, cae a una equivalente por rol.
    from app.routers.fases import resolver_clave
    lead.estado = resolver_clave(db, lead.estado)

    if completo or pidio_humano:
        lead.escalated = True
        # "Califica y entrega": el bot cede la conversación al asesor humano.
        lead.bot_active = False
        if conv is not None:
            conv.bot_active = False
        # Qué se notifica, a quién y con qué mensaje de cierre lo decide LA FASE.
        from app.services import fases_acciones
        fases_acciones.al_entrar_a_fase(db, lead, incompleto=not completo,
                                        canal=channel, enviar=enviar)

    db.commit()

    # Enviar la respuesta (en burbujas). En modo prueba (enviar=False) solo se guarda en el panel.
    try:
        from app.services import messaging_out
        for bubble in _split_bubbles(reply):
            if enviar:
                messaging_out.send_text(db, phone, bubble, channel=channel, name=name)
            else:
                db.add(ChatMessage(phone=phone, channel=channel,
                                   direction=MessageDirection.outbound, body=bubble))
                db.commit()
        logger.warning("[bot] respuesta %s a %s (msg #%s)",
                       "enviada" if enviar else "SIMULADA", phone, lead.message_count)
    except Exception as e:  # noqa: BLE001
        logger.error("[bot] error enviando mensaje: %s", e)


# El aviso al admin vive ahora en `app/services/fases_acciones.py`: lo decide la fase, no el bot.
