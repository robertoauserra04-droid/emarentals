"""El prefijo del system prompt tiene que ser ESTABLE, o la caché de OpenAI no sirve (B-5).

Por qué existe este archivo. Este prompt es grande —identidad, catálogo, estilo, el cuestionario
completo y el contexto vivo del panel— y se reenvía ENTERO en cada mensaje y en cada ronda del
loop (hasta 4). Casi todo el costo del bot está ahí. OpenAI cobra ese texto mucho más barato si
lo reconoce como repetido, pero para reconocerlo compara PREFIJOS EXACTOS: basta que cambie un
carácter arriba para que todo lo de abajo deje de contar como caché, aunque sean idénticos.

El bug: el prompt abría con `Fecha y hora actual: ..., 14:07.` en su TERCERA línea. El prefijo
cambiaba cada 60 segundos, así que la caché no pegaba nunca y todo se pagaba a tarifa completa.
No lo delataba ningún síntoma: el bot contestaba bien, solo costaba de más.

Estos tests son el candado para que nadie vuelva a meter un dato volátil arriba del prompt sin
enterarse. Si algún día EMA necesita la hora, va al FINAL — y este archivo lo obliga.

Es el primer `test_prompt_caching.py` de la flota; los otros repos lo copian de aquí.
"""
import re

from app.models.lead import EmaLead
from app.services.bot.prompt import build_system_prompt

# Un reloj: "14:07", "9:30". La fecha con barras ("19/8/2026") no empata con esto.
_HORA = re.compile(r"\b\d{1,2}:\d{2}\b")

# Con cuánto prefijo estable nos damos por satisfechos. OpenAI no cachea prefijos cortos, así que
# esto no es un número decorativo: por debajo de ~1000 tokens no hay descuento que valga.
_PREFIJO_MINIMO = 2000


def test_el_prompt_no_lleva_la_hora():
    """La causa raíz de B-5. EMA no agenda, no cotiza y no consulta disponibilidad: no hay una
    sola regla del bot que necesite saber el minuto."""
    assert not _HORA.search(build_system_prompt())


def test_dos_llamadas_seguidas_dan_el_mismo_prompt():
    """Mismo lead y mismo catálogo → texto idéntico. Antes, dos turnos separados por un minuto
    producían dos prompts distintos y la caché no pegaba en ninguno."""
    lead = EmaLead(phone="1", tipo_propiedad="oficina", oficina_m2=200, tiempo_renta="12+")
    assert build_system_prompt(lead) == build_system_prompt(lead)


def test_la_fecha_si_esta():
    """Quitar la hora no es quitar el calendario: la fecha se conserva y cambia una vez al día."""
    assert "Fecha de hoy:" in build_system_prompt()


def test_lo_que_cambia_por_lead_va_abajo():
    """Lo variable tiene que ir DESPUÉS de lo estable, o parte el prefijo cacheable.

    El estado del lead ("lo que ya sabes / te falta preguntar") cambia en cada turno. Si subiera
    arriba del catálogo, todo lo de abajo dejaría de cachearse aunque fuera idéntico.
    """
    lead = EmaLead(phone="2", tipo_propiedad="oficina", oficina_m2=200, tiempo_renta="12+")
    p = build_system_prompt(lead)
    variable = p.index("LO QUE YA SABES")
    assert variable > _PREFIJO_MINIMO, (
        f"la parte variable empieza en el carácter {variable}: queda muy poco prefijo que cachear")
    # Y el catálogo y el estilo, que son lo pesado y lo estable, van antes.
    assert p.index("ESTILO (MUY IMPORTANTE)") < variable


def test_el_prefijo_es_el_mismo_para_dos_leads_distintos():
    """Dos prospectos distintos comparten el mismo encabezado: eso es lo que se cachea entre
    conversaciones, no solo entre turnos de la misma."""
    a = build_system_prompt(EmaLead(phone="3", tipo_propiedad="casa", recamaras=3))
    b = build_system_prompt(EmaLead(phone="4", tipo_propiedad="oficina", oficina_personas=25))
    assert a[:_PREFIJO_MINIMO] == b[:_PREFIJO_MINIMO]


def test_el_primer_contacto_no_parte_el_prefijo():
    """`es_primer_contacto` cambia una línea; tiene que ser la última, no una de arriba."""
    a = build_system_prompt(es_primer_contacto=True)
    b = build_system_prompt(es_primer_contacto=False)
    assert a[:_PREFIJO_MINIMO] == b[:_PREFIJO_MINIMO]
