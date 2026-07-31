"""Debounce del turno del bot (portado de bienesraicesEnrique/inbound.py).

Kapso entrega cada mensaje como un webhook aparte. Sin debounce, si el prospecto manda 3
mensajes seguidos, el bot responde 3 veces (y quema 3× tokens). Aquí se agrupa la ráfaga: cada
mensaje (re)programa un timer; cuando pasan `DEBOUNCE` segundos sin mensajes nuevos, corre UN
solo turno del bot (que ya ve todos los mensajes en el historial, porque el webhook los persistió
antes de llamar aquí).

⚠️ Buffers/locks EN PROCESO → válido con UNA réplica (como el resto de la flota). El turno corre
con su PROPIA sesión de BD (la del request ya se cerró) y en un hilo (no bloquea el event loop).
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

DEBOUNCE_SEG = 2.0

_timers: dict[str, asyncio.Task] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(phone: str) -> asyncio.Lock:
    return _locks.setdefault(phone, asyncio.Lock())


async def programar(phone: str, text: str, name: str | None, campania_id: int | None,
                    channel: str = "whatsapp") -> None:
    """(Re)programa el turno del bot para `phone`. Cancela el timer previo si aún no dispara."""
    tarea = _timers.get(phone)
    if tarea and not tarea.done():
        tarea.cancel()
    _timers[phone] = asyncio.create_task(_disparar(phone, text, name, campania_id, channel))


async def _disparar(phone: str, text: str, name: str | None, campania_id: int | None,
                    channel: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SEG)
    except asyncio.CancelledError:
        return  # llegó otro mensaje: este turno se descarta y se reprograma
    async with _lock(phone):  # nunca dos turnos en paralelo del mismo prospecto
        await asyncio.to_thread(_procesar, phone, text, name, campania_id, channel)
    _timers.pop(phone, None)


def _procesar(phone: str, text: str, name: str | None, campania_id: int | None,
              channel: str) -> None:
    from app.database import SessionLocal
    from app.services.bot import handler
    db = SessionLocal()
    try:
        handler.handle_inbound(db, phone, text, name=name, channel=channel, campania_id=campania_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[debounce] error en el turno de %s: %s", phone, e)
    finally:
        db.close()
