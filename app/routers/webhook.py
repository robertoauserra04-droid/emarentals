"""Webhook de WhatsApp (Kapso) — entrada de mensajes del prospecto.

Verifica la firma HMAC, registra el inbound, detecta el eco saliente del negocio (coexistencia:
el asesor respondió por WhatsApp Business → pausa el bot), guarda el mensaje y dispara al bot.
"""
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.lead import EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.security.webhook import verificar_firma
from app.services import recovery
from app.services.bot import leads

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])

# Kapso firma en X-Webhook-Signature; las demás son respaldo (Meta/otros).
_SIGNATURE_HEADERS = ("x-webhook-signature", "x-hub-signature-256", "x-kapso-signature", "x-signature")


def _firma(headers) -> str | None:
    for h in _SIGNATURE_HEADERS:
        v = headers.get(h)
        if v:
            return v
    return None


@router.get("")
async def verify(request: Request):
    """Verificación inicial del webhook (Meta/Kapso handshake)."""
    params = request.query_params
    if params.get("hub.verify_token") == settings.webhook_verify_token:
        return int(params.get("hub.challenge", 0))
    return {"ok": True}


def _nombre_kapso(conv: dict, msg: dict, phone: str) -> str | None:
    conv_k = conv.get("kapso") or {}
    msg_k = msg.get("kapso") or {}
    contactos = conv.get("contacts") or []
    c0 = contactos[0] if contactos else {}
    candidatos = [
        (conv.get("metadata") or {}).get("customer_name"),
        conv.get("contact_name"), conv_k.get("contact_name"), msg_k.get("contact_name"),
        (c0.get("profile") or {}).get("name"), (conv.get("profile") or {}).get("name"),
        conv.get("username"),
    ]
    for c in candidatos:
        if c and isinstance(c, str) and c.strip() and c.strip() != phone:
            return c.strip()
    return None


def _extraer_kapso(payload: dict) -> dict | None:
    """Payload v2 de Kapso: {message:{...}, conversation:{...}} (a veces envuelto en 'data')."""
    msg = payload.get("message") or {}
    conv = payload.get("conversation") or {}
    kap = msg.get("kapso") or {}
    wamid = msg.get("id") or msg.get("whatsapp_message_id")
    tipo = msg.get("type") or msg.get("message_type") or "text"
    text = ((msg.get("text") or {}).get("body")
            or kap.get("content") or msg.get("content") or "")
    audio_url = None
    if tipo in ("audio", "voice"):
        media = msg.get(tipo) or {}
        audio_url = media.get("url") or media.get("link")
    direction = (kap.get("direction") or msg.get("direction") or "inbound").lower()
    origin = kap.get("origin")  # cloud_api = eco del propio bot; business_app = humano
    raw_phone = (conv.get("phone_number") or msg.get("from") or "").replace(" ", "")
    phone = raw_phone.lstrip("+")
    if not phone:
        return None
    if direction != "inbound":
        # Saliente: eco del bot (cloud_api) se ignora; humano (business_app u otro) pausa el bot.
        if origin == "cloud_api":
            return None
        return {"direccion": "outbound", "telefono_destino": phone, "text": text, "wamid": wamid}
    name = _nombre_kapso(conv, msg, phone)
    return {"direccion": "inbound", "phone": phone, "text": text, "name": name,
            "audio_url": audio_url, "wamid": wamid}


def _extraer_nombre(entry: dict, msg: dict, phone: str) -> str | None:
    """Nombre del perfil de WhatsApp (multi-ruta, patrón de aseguradora). None si no hay uno real."""
    conv = entry.get("conversation") or {}
    contactos = entry.get("contacts") or conv.get("contacts") or []
    c0 = contactos[0] if contactos else {}
    candidatos = [
        (c0.get("profile") or {}).get("name"),
        conv.get("contact_name"),
        (conv.get("metadata") or {}).get("customer_name"),
        (msg.get("kapso") or {}).get("contact_name"),
        msg.get("pushname"),
    ]
    for c in candidatos:
        if c and isinstance(c, str) and c.strip() and c.strip() != phone:
            return c.strip()
    return None


def _es_eco_saliente(msg: dict) -> bool:
    """¿El mensaje lo mandó el NEGOCIO (asesor por WhatsApp Business), no el prospecto?

    Cinturón de robustez portado de bienesraicesEnrique: Kapso reenvía los mensajes salientes
    (message.sent) como webhooks; si se procesan como entrantes, el bot se responde a sí mismo en
    bucle. Se detecta por varias señales; ante la duda se asume ENTRANTE (seguro).
    """
    if msg.get("from_me") is True or msg.get("echo") is True:
        return True
    if str(msg.get("direction", "")).lower() in ("outbound", "out", "sent"):
        return True
    negocio = {str(settings.kapso_phone_number_id)} - {"", "None"}
    if negocio and str(msg.get("from")) in negocio and (msg.get("to") or msg.get("recipient_id")):
        return True
    return False


def _extraer(payload: dict) -> dict | None:
    """Normaliza el payload de Kapso. Marca la dirección y captura el wamid (dedup).

    ENTRANTE (prospecto): {direccion:'inbound', phone, text, name, audio_url, wamid}.
    SALIENTE (negocio/asesor por coexistencia): {direccion:'outbound', telefono_destino, text, wamid}.
    None si no es un mensaje (p.ej. reportes de estado 'statuses').
    """
    # Kapso a veces envuelve el evento en 'data'.
    if "message" not in payload and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    # Formato Kapso v2: {message, conversation}.
    if isinstance(payload.get("message"), dict):
        return _extraer_kapso(payload)
    # Formato Meta Cloud API (respaldo).
    try:
        entry = payload["entry"][0]["changes"][0]["value"]
        if entry.get("statuses") is not None:
            return None  # reporte de entrega/lectura, no un mensaje
        msg = entry["messages"][0]
        wamid = msg.get("id")
        tipo = msg.get("type")
        text = ""
        audio_url = None
        if tipo == "text":
            text = (msg.get("text") or {}).get("body", "")
        elif tipo == "button":
            text = (msg.get("button") or {}).get("text", "")
        elif tipo in ("audio", "voice"):
            media = msg.get(tipo) or {}
            audio_url = media.get("url") or media.get("link")

        # Eco saliente del negocio → coexistencia (el asesor tomó el chat por WhatsApp Business).
        if _es_eco_saliente(msg):
            destino = msg.get("to") or msg.get("recipient_id")
            if not destino:
                return None
            return {"direccion": "outbound", "telefono_destino": str(destino),
                    "text": text, "wamid": wamid}

        phone = msg["from"]
        name = _extraer_nombre(entry, msg, str(phone))
        return {"direccion": "inbound", "phone": phone, "text": text, "name": name,
                "audio_url": audio_url, "wamid": wamid}
    except (KeyError, IndexError, TypeError):
        return None


@router.post("")
async def inbound(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    firma = _firma(request.headers)
    if not verificar_firma(body, firma):
        logger.warning("[webhook] firma inválida/ausente, rechazado. headers=%s",
                       list(request.headers.keys()))
        return {"ok": False, "error": "firma inválida"}

    payload = await request.json()
    data = _extraer(payload)
    if not data:
        return {"ok": True, "ignored": True}

    # Dedup: si ya procesamos este wamid, ignorar (Kapso/Meta pueden reenviar el mismo webhook).
    wamid = data.get("wamid")
    if wamid and db.query(ChatMessage).filter(ChatMessage.wamid == wamid).first():
        logger.warning("[webhook] wamid %s ya procesado, ignorado (dedup)", wamid)
        return {"ok": True, "duplicated": True}

    # Eco saliente del negocio (coexistencia): el asesor respondió por WhatsApp Business.
    # Se registra el saliente, se PAUSA el bot de esa conversación y NO se dispara la IA.
    if data["direccion"] == "outbound":
        destino = data["telefono_destino"]
        conv = db.query(Conversation).filter(Conversation.phone == destino).first()
        if conv is None:
            conv = Conversation(phone=destino)
            db.add(conv)
        conv.bot_active = False
        lead_e = db.query(EmaLead).filter(EmaLead.phone == destino).first()
        if lead_e:
            lead_e.bot_active = False
        db.add(ChatMessage(phone=destino, direction=MessageDirection.outbound,
                           body=data.get("text") or "", wamid=wamid))
        db.commit()
        logger.warning("[webhook] eco saliente a %s → bot pausado (coexistencia)", destino)
        return {"ok": True, "coexistence": True}

    phone, text, name = data["phone"], data["text"], data["name"]
    if not phone:
        return {"ok": True, "ignored": True}

    # Nota de voz → transcribir (Whisper). El texto transcrito entra como si lo hubiera escrito.
    if not text and data.get("audio_url"):
        from app.services import transcription
        transcrito = transcription.transcribir(data["audio_url"])
        text = transcrito or "(nota de voz — no se pudo transcribir)"

    if not text:
        return {"ok": True, "ignored": True}

    # Guardar el inbound + conversación (para el panel).
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    if conv is None:
        conv = Conversation(phone=phone, name=name)
        db.add(conv)
    db.add(ChatMessage(phone=phone, direction=MessageDirection.inbound, body=text, wamid=wamid))
    db.commit()

    # Registrar el lead + cancelar recuperación / detectar opt-out con este inbound.
    lead = leads.get_or_create_lead(db, phone, name=name)
    recovery.register_inbound(db, lead, text)
    db.commit()

    # Disparar el bot con DEBOUNCE: agrupa ráfagas (una sola respuesta al burst).
    from app.services import debounce
    await debounce.programar(phone, text, name, None, channel="whatsapp")
    return {"ok": True}
