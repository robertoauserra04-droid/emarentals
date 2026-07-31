"""Motor conversacional — OpenAI tool-calling (gpt-4o-mini).

Port del patrón de vella panel (Bell) con el loop multi-ronda de psicología: 1ª llamada con
tools; si la IA invoca alguna, el handler la ejecuta (callback) y se repite hasta `_MAX_RONDAS`,
luego una llamada final para el texto en lenguaje natural.

Tools v1 (mínimas): `capturar_lead` (clasifica al prospecto) y `alertar_asesor` (buen lead →
alerta al admin + cede al humano). EMA Rentals NO agenda, NO cotiza, NO manda fichas.
"""
import json
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_MAX_RONDAS = 4


def _model() -> str:
    return settings.openai_model


CAPTURAR_LEAD_TOOL = {
    "type": "function",
    "function": {
        "name": "capturar_lead",
        "description": (
            "Registra o actualiza los datos del prospecto con lo que sabes de la conversación. "
            "Llámala en cuanto tengas un dato nuevo (segmento, necesidad, plazo, fecha, zona, "
            "presupuesto, interés). No inventes datos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre de la persona, si lo dio"},
                "segmento": {
                    "type": "string",
                    "enum": ["residencial", "oficina", "airbnb", "corporativo"],
                    "description": "Para qué es: casa (residencial), oficina, propiedad de Airbnb, o proyecto corporativo",
                },
                "necesidad": {
                    "type": "string",
                    "enum": ["pieza_suelta", "paquete", "casa_completa", "oficina_completa"],
                    "description": "Cuánto necesita: una pieza suelta, un paquete, o amueblar completo",
                },
                "plazo_meses": {
                    "type": "integer",
                    "description": "Por cuántos meses quiere la renta (3 a 24). Plazo largo = mejor prospecto.",
                },
                "fecha_entrega": {
                    "type": "string",
                    "enum": ["ya", "1-4sem", ">1mes"],
                    "description": "Qué tan pronto necesita la entrega",
                },
                "zona": {"type": "string", "description": "Ciudad/zona de entrega (ej. Monterrey, CDMX)"},
                "presupuesto": {
                    "type": "string",
                    "description": "Presupuesto o rango que declaró, tal como lo dijo (ej. 'unos 5 mil al mes')",
                },
                "nivel_interes": {
                    "type": "string",
                    "enum": ["Alto", "Medio", "Bajo"],
                    "description": "Qué tan interesada se ve la persona",
                },
                "estado": {
                    "type": "string",
                    "enum": ["nuevo", "interesado", "calificado", "ganado", "perdido"],
                    "description": (
                        "En qué punto va. Usa 'calificado' SOLO si ya se ve un buen prospecto real "
                        "(sabe qué necesita, por cuánto tiempo y para cuándo). El sistema valida "
                        "que haya señales reales antes de avisar a un asesor."
                    ),
                },
                "que_pregunto": {"type": "string", "description": "Qué le interesó o preguntó (breve)"},
                "resumen": {"type": "string", "description": "Resumen en 1-2 frases de lo que necesita"},
                "motivo_perdida": {
                    "type": "string",
                    "description": "Si se va o pierde interés, por qué",
                },
            },
            "required": [],
        },
    },
}

ALERTAR_ASESOR_TOOL = {
    "type": "function",
    "function": {
        "name": "alertar_asesor",
        "description": (
            "Avisa a un asesor humano de EMA Rentals para que retome la conversación. Llámala "
            "cuando el prospecto ya está listo para avanzar (quiere una propuesta o cotización, "
            "pide hablar con una persona), o cuando ya no tengas nada nuevo que aportar y sea un "
            "buen prospecto. El sistema avisa al equipo automáticamente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "motivo": {"type": "string", "description": "Por qué se pasa a un asesor (breve)"},
            },
            "required": [],
        },
    },
}

TOOLS = [CAPTURAR_LEAD_TOOL, ALERTAR_ASESOR_TOOL]


def _client() -> OpenAI:
    return OpenAI(api_key=settings.openai_api_key, timeout=20)


def generate_reply(system: str, history: list[dict], handlers: dict) -> str:
    """Genera la respuesta del bot. `handlers` mapea nombre de tool → callable(args)->str."""
    client = _client()
    messages = [{"role": "system", "content": system}] + history

    for _ in range(_MAX_RONDAS):
        resp = client.chat.completions.create(
            model=_model(),
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=400,
            temperature=0.5,   # más bajo que el molde: tono formal, respuestas consistentes
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return (msg.content or "").strip()

        messages.append({
            "role": "assistant",
            "content": msg.content or None,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })
        for tc in msg.tool_calls:
            result = "ok"
            handler = handlers.get(tc.function.name)
            if handler:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    result = handler(args)
                except Exception as e:  # noqa: BLE001
                    logger.error("[bot.ai] error en %s: %s", tc.function.name, e)
                    result = "error interno; discúlpate y ofrece intentar de nuevo"
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    final = client.chat.completions.create(
        model=_model(), messages=messages, max_tokens=400, temperature=0.5
    )
    return (final.choices[0].message.content or "").strip()
