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


def _mensaje_cierre(db: Session, lead: EmaLead, fase, canal: str, enviar: bool) -> None:
    """Manda el texto de cierre de la fase, UNA sola vez por fase.

    Lo manda el código y no el modelo a propósito: un mensaje de descarte que salga cuando no
    toca le pega al cliente de frente, y un LLM se puede desviar. Por eso no se configura desde
    Contexto sino aquí.
    """
    if not fase or not fase.mensaje_cierre:
        return
    if lead.cierre_enviado_en == fase.clave:
        return
    lead.cierre_enviado_en = fase.clave
    if not enviar:
        return
    try:
        from app.services import messaging_out
        messaging_out.send_text(db, lead.phone, fase.mensaje_cierre, channel=canal, name=lead.name)
    except Exception as e:  # noqa: BLE001
        logger.error("[fases] no se pudo mandar el cierre a %s: %s", lead.phone, e)


def al_entrar_a_fase(db: Session, lead: EmaLead, incompleto: bool = False,
                     canal: str = "whatsapp", enviar: bool = True) -> None:
    """El lead acaba de caer en `lead.estado`. Ejecuta lo que esa fase tenga configurado.

    `incompleto=True` = el prospecto pidió un asesor sin terminar el cuestionario. En ese caso se
    avisa SIEMPRE, tenga la fase la notificación encendida o no: acabamos de apagar el bot y si no
    avisamos nadie lo atiende.
    """
    from app.routers.fases import fase_por_clave

    fase = fase_por_clave(db, lead.estado)
    if incompleto or (fase is not None and fase.notificar):
        _notificar(db, lead, fase, incompleto)
    # Si el cuestionario quedó a medias no mandamos el cierre de la fase: el lead está en
    # "Interesado" de paso, no clasificado ahí.
    if not incompleto:
        _mensaje_cierre(db, lead, fase, canal, enviar)
