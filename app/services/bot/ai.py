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
            "Registra o actualiza los datos del prospecto conforme lo vas filtrando. Llámala en "
            "cuanto tengas un dato nuevo (tipo de propiedad, recámaras, m²/personas, tiempo de renta). "
            "No inventes datos. El sistema clasifica solo; tú solo registra lo que la persona diga."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre de la persona, si lo dio"},
                "tipo_propiedad": {
                    "type": "string",
                    "enum": ["oficina", "departamento", "casa"],
                    "description": "Para qué tipo de propiedad es la renta",
                },
                "recamaras": {
                    "type": "integer",
                    "description": "Solo departamento o casa: número de recámaras",
                },
                "oficina_m2": {
                    "type": "integer",
                    "description": "Solo oficina: metros cuadrados aproximados",
                },
                "oficina_personas": {
                    "type": "integer",
                    "description": "Solo oficina: cuántas personas trabajarán ahí",
                },
                "tiempo_renta": {
                    "type": "string",
                    "enum": ["0-6", "6-12", "12+"],
                    "description": "Cuánto tiempo quiere rentar: '0-6' (6 meses o menos), "
                                   "'6-12' (6 a 12 meses), '12+' (12 meses o más)",
                },
                "uso": {
                    "type": "string",
                    "enum": ["reventa", "propio"],
                    "description": "Si pondrá los muebles frente a SUS clientes/unidades (reventa: "
                                   "Airbnb, desarrollador, coworking) o si los usará él mismo (propio). Solo si es claro.",
                },
                "zona": {"type": "string", "description": "Ciudad/zona de entrega (ej. Monterrey, CDMX)"},
                "nivel_interes": {
                    "type": "string",
                    "enum": ["Alto", "Medio", "Bajo"],
                    "description": "Qué tan interesada se ve la persona",
                },
                "que_pregunto": {"type": "string", "description": "Qué le interesó o preguntó (breve)"},
                "resumen": {"type": "string", "description": "Resumen en 1-2 frases de lo que necesita"},
                "motivo_perdida": {
                    "type": "string",
                    "description": "Solo si la persona ya no está interesada: por qué",
                },
                "solo_informacion": {
                    "type": "boolean",
                    "description": "true SOLO si la persona dice explícitamente que no busca "
                                   "rentar ahora y únicamente quiere información, o si se niega "
                                   "a contestar las preguntas del filtro. NO lo pongas solo "
                                   "porque todavía no haya contestado: para eso está el silencio.",
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
            "Pasa la conversación a un asesor humano ANTES de terminar las preguntas. Llámala "
            "SOLO si el prospecto lo pide explícitamente: quiere hablar con una persona, insiste "
            "en que le den precios, o se niega a seguir contestando. "
            "NO la llames al terminar el cuestionario ni para despedirte: cuando ya tienes el "
            "tipo de propiedad, su dato ligado y el tiempo de renta, el sistema pasa el prospecto "
            "al asesor por su cuenta. Llamarla de más hace que se avise a un asesor con datos "
            "incompletos."
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
