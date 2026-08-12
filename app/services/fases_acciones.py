"""Qué pasa cuando un lead entra a una fase. Punto ÚNICO de entrada.

La fase manda: si se notifica, a quién, y qué mensaje de cierre recibe el prospecto. Antes esto
vivía hardcodeado en el handler (`if actions["alertar"] or lead.es_buen_prospecto`) y no se podía
configurar sin tocar código.

Esto se dispara SOLO cuando el bot clasifica. Mover una tarjeta a mano en el Kanban no manda
nada: arrastrar una columna no debe dispararle un WhatsApp al cliente por accidente.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.lead import EmaLead
from app.services import notificaciones

logger = logging.getLogger(__name__)


def _notificar(db: Session, lead: EmaLead, fase, incompleto: bool) -> None:
    """Idempotente: `alertado_at` marca que ya se avisó de este lead."""
    if lead.alertado_at is not None:
        return
    try:
        notificaciones.alertar_admin(lead, db=db, fase=fase, incompleto=incompleto)
        lead.alertado_at = datetime.now(timezone.utc)
        logger.warning("[fases] aviso enviado por %s (fase %s, incompleto=%s)",
                       lead.phone, lead.estado, incompleto)
    except Exception as e:  # noqa: BLE001
        logger.error("[fases] no se pudo avisar de %s: %s", lead.phone, e)


def _mensaje_cierre(db: Session, lead: EmaLead, fase, canal: str, enviar: bool) -> bool:
    """Manda el texto de cierre de la fase, UNA sola vez por fase. True si salió.

    Lo manda el código y no el modelo a propósito: un mensaje de descarte que salga cuando no
    toca le pega al cliente de frente, y un LLM se puede desviar. Por eso no se configura desde
    Contexto sino aquí.

    Devolver True importa: el handler descarta la despedida del modelo de ese mismo turno. Sin
    eso el prospecto recibe DOS mensajes — idénticos en las fases de asesor (el prompt le pide
    al modelo la misma frase que trae `_CIERRE_ASESOR`) y CONTRADICTORIOS en las de descarte
    ("un asesor se pondrá en contacto" seguido de "no cumple con nuestros criterios").
    """
    if not fase or not fase.mensaje_cierre:
        return False
    if lead.cierre_enviado_en == fase.clave:
        return False
    lead.cierre_enviado_en = fase.clave

    # El cierre se registra en la caja negra igual que las burbujas del bot: antes era el único
    # mensaje que le llegaba al prospecto sin dejar rastro, y por eso el timeline de un turno
    # duplicado se veía impecable.
    import observabilidad as caja
    caja.registrar("mensaje_saliente", {"texto": fase.mensaje_cierre, "origen": "fase",
                                        "fase": fase.clave, "simulado": not enviar})

    if not enviar:
        # Simulador del panel: no sale por WhatsApp, pero sí se guarda, para que el simulador
        # muestre exactamente lo que recibiría el prospecto (cierre incluido).
        from app.models.messaging import ChatMessage, MessageDirection
        db.add(ChatMessage(phone=lead.phone, channel=canal,
                           direction=MessageDirection.outbound, body=fase.mensaje_cierre))
        db.commit()
        return True
    try:
        from app.services import messaging_out
        messaging_out.send_text(db, lead.phone, fase.mensaje_cierre, channel=canal, name=lead.name)
    except Exception as e:  # noqa: BLE001
        logger.error("[fases] no se pudo mandar el cierre a %s: %s", lead.phone, e)
        return False   # que hable el modelo: mejor su despedida que dejar al prospecto sin nada
    return True


def al_entrar_a_fase(db: Session, lead: EmaLead, incompleto: bool = False,
                     canal: str = "whatsapp", enviar: bool = True) -> bool:
    """El lead acaba de caer en `lead.estado`. Ejecuta lo que esa fase tenga configurado.

    `incompleto=True` = el prospecto pidió un asesor sin terminar el cuestionario. En ese caso se
    avisa SIEMPRE, tenga la fase la notificación encendida o no: acabamos de apagar el bot y si no
    avisamos nadie lo atiende.

    Devuelve True si la fase mandó su mensaje de cierre: ese es el último mensaje de la
    conversación y el bot ya no debe añadir el suyo encima.
    """
    from app.routers.fases import fase_por_clave

    fase = fase_por_clave(db, lead.estado)
    if incompleto or (fase is not None and fase.notificar):
        _notificar(db, lead, fase, incompleto)
    # Si el cuestionario quedó a medias no mandamos el cierre de la fase: el lead está en
    # "Interesado" de paso, no clasificado ahí. Ahí sí cierra el modelo.
    if not incompleto:
        return _mensaje_cierre(db, lead, fase, canal, enviar)
    return False
