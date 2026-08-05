"""prompt_ia / respuesta_ia / metrica de tokens sin tocar los motores, uno por uno.

El problema: los 18 repos llaman a OpenAI de formas distintas — con tools y bucle de
rondas, con comandos embebidos y regex, con una sola llamada, con varias llamadas de
corrección… Instrumentar cada sitio a mano es frágil (uno se olvida, y el que se olvida
es justo el turno que no vas a poder reproducir).

La solución: envolver `Completions.create` una vez. Se registra CADA llamada al modelo,
venga de donde venga: el motor del bot, el asistente del panel, el estimador, un job.
`FORMATO.md` es explícito en que `prompt_ia` guarda `system` y `messages` EXACTOS — eso
es justo lo que pasa por aquí, sin resumir.

Se llama UNA vez al arrancar, después de crear la app:

    try:
        from caja_ia import instrumentar_openai   # o app./core.
        instrumentar_openai()
    except Exception:
        pass

Idempotente y a prueba de fallos: si `openai` no está instalado, si la versión no tiene
la ruta esperada o si el registro truena, no pasa nada y la llamada sigue igual.
"""

from __future__ import annotations

import functools

_YA = False


def _resumen_mensajes(messages):
    """Los messages tal cual, con una nota de tamaño para leerlos de un vistazo.

    No se recortan: el core ya trunca a 64 KB por evento, y un prompt resumido no
    sirve para replayar.
    """
    if not isinstance(messages, (list, tuple)):
        return messages
    return {"n": len(messages), "messages": list(messages)}


def _nombres_tools(tools):
    try:
        return [t.get("function", {}).get("name") for t in (tools or [])]
    except Exception:  # noqa: BLE001
        return None


def _antes(caja, kwargs, componente):
    caja.registrar("prompt_ia", {
        "componente": componente,
        "modelo": kwargs.get("model"),
        "tools": _nombres_tools(kwargs.get("tools")),
        "tool_choice": kwargs.get("tool_choice"),
        "temperature": kwargs.get("temperature"),
        **(_resumen_mensajes(kwargs.get("messages")) or {}),
    })


def _despues(caja, resp, componente):
    try:
        msg = resp.choices[0].message if resp.choices else None
        caja.registrar("respuesta_ia", {
            "componente": componente,
            "texto": getattr(msg, "content", None),
            "finish_reason": resp.choices[0].finish_reason if resp.choices else None,
            "tool_calls": [tc.function.name for tc in (getattr(msg, "tool_calls", None) or [])],
        })
        uso = getattr(resp, "usage", None)
        if uso:
            caja.registrar("metrica", {
                "accion": "tokens_ia", "componente": componente,
                "tokens": getattr(uso, "total_tokens", None),
                "tokens_entrada": getattr(uso, "prompt_tokens", None),
                "tokens_salida": getattr(uso, "completion_tokens", None),
            })
    except Exception:  # noqa: BLE001
        pass


def instrumentar_openai(componente: str = "ia") -> bool:
    """Envuelve Completions.create (sync y async). Devuelve si quedó instrumentado."""
    global _YA
    if _YA:
        return True
    try:
        import observabilidad as caja
    except Exception:  # noqa: BLE001
        return False
    if not caja.activa():
        return False
    try:
        from openai.resources.chat.completions import AsyncCompletions, Completions
    except Exception:  # noqa: BLE001
        return False

    def envolver(clase, es_async):
        original = clase.create
        if getattr(original, "_caja_negra", False):
            return
        if es_async:
            @functools.wraps(original)
            async def create(self, *args, **kwargs):
                try:
                    _antes(caja, kwargs, componente)
                except Exception:  # noqa: BLE001
                    pass
                resp = await original(self, *args, **kwargs)
                _despues(caja, resp, componente)
                return resp
        else:
            @functools.wraps(original)
            def create(self, *args, **kwargs):
                try:
                    _antes(caja, kwargs, componente)
                except Exception:  # noqa: BLE001
                    pass
                resp = original(self, *args, **kwargs)
                _despues(caja, resp, componente)
                return resp
        create._caja_negra = True
        clase.create = create

    try:
        envolver(Completions, False)
        envolver(AsyncCompletions, True)
    except Exception:  # noqa: BLE001
        return False
    _YA = True
    return True
