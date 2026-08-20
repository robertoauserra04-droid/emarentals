"""El esquema de `capturar_lead` es una GARANTÍA, no una sugerencia (B-1 y B-4).

El bug que cubre este archivo era silencioso y caro. El esquema declaraba
`enum: ["0-6", "6-12", "12+"]` para `tiempo_renta`, pero sin `strict: true` ese enum era
documentación para el modelo, no una restricción de la API. Un `tiempo_renta="un año"` se
guardaba literal y a partir de ahí el error se propagaba solo:

  1. `cuestionario_completo()` → True (el campo no está vacío)
  2. `fase_calificada()` pregunta `== "12+"`, no empata, y manda el lead a Mid en vez de Bueno
  3. `TABLA_PLAZO.get("un año", 0)` → 0 puntos: pierde los 35 del plazo
  4. la FASE decide a quién se avisa y qué cierre recibe el prospecto → aviso y mensaje de
     OTRA fase

Y nada en el panel lo delataba: el lead se ve completo, con sus datos, en la columna
equivocada. Por eso la prueba no es "que se guarde bien": es que el valor malo NO PUEDA LLEGAR.
"""
from app.models.lead import EmaLead
from app.services.bot import leads
from app.services.bot.ai import ALERTAR_ASESOR_TOOL, CAPTURAR_LEAD_TOOL


# ─────────── El esquema cumple el contrato de modo estricto ───────────

def test_capturar_lead_es_estricto():
    """Las tres condiciones que OpenAI exige juntas; si falta una, `strict` no aplica."""
    fn = CAPTURAR_LEAD_TOOL["function"]
    params = fn["parameters"]
    assert fn["strict"] is True
    assert params["additionalProperties"] is False
    # En modo estricto TODAS las propiedades van en `required`; lo opcional se expresa con null.
    assert set(params["required"]) == set(params["properties"])


def test_alertar_asesor_es_estricto():
    fn = ALERTAR_ASESOR_TOOL["function"]
    assert fn["strict"] is True
    assert fn["parameters"]["additionalProperties"] is False
    assert set(fn["parameters"]["required"]) == set(fn["parameters"]["properties"])


def test_todo_campo_admite_null():
    """`required` completo solo es compatible con campos opcionales si aceptan null."""
    for campo, prop in CAPTURAR_LEAD_TOOL["function"]["parameters"]["properties"].items():
        assert "null" in prop["type"], f"{campo} no admite null y está en required"
        if "enum" in prop:
            assert None in prop["enum"], f"el enum de {campo} no incluye null"


def test_los_rangos_de_plazo_siguen_siendo_los_del_score():
    """El enum del esquema y `TABLA_PLAZO` tienen que hablar del mismo catálogo.

    Si alguien agrega un rango al esquema y no a la tabla, ese plazo vale 0 puntos en silencio:
    exactamente el efecto de B-1, pero por otra puerta.
    """
    del_esquema = leads.CATALOGO_CAMPOS["tiempo_renta"]
    assert del_esquema == set(leads.TABLA_PLAZO)


def test_los_tipos_de_propiedad_del_esquema_puntuan_todos():
    assert leads.CATALOGO_CAMPOS["tipo_propiedad"] == set(leads.TABLA_TIPO)


# ─────────── La segunda red: un valor fuera de catálogo se descarta, no se guarda ───────────

def test_plazo_fuera_de_catalogo_no_se_guarda():
    """El caso canónico de B-1. Antes esto guardaba "un año" y arruinaba la clasificación."""
    l = EmaLead(phone="1")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "oficina", "oficina_m2": 200,
                                  "tiempo_renta": "un año"})
    assert l.tiempo_renta is None                    # descartado, no guardado literal
    assert leads.cuestionario_completo(l) is False   # y por tanto NO se clasifica
    assert "tiempo de renta" in leads.falta_del_cuestionario(l)


def test_el_caso_canonico_de_ema_cae_en_la_columna_correcta():
    """Oficina de 200 m² a doce meses: el mejor lead que puede recibir EMA.

    Con el plazo bien traducido va a `oficina_bueno` con score 91. Antes, escrito como
    "un año", caía en `oficina_mid` con 56 — y el aviso salía al destinatario de otra fase.
    """
    l = EmaLead(phone="2")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "oficina", "oficina_m2": 200,
                                  "tiempo_renta": "12+"})
    assert l.es_buen_prospecto is True
    assert l.estado == leads.FASE_BUENO_OFI
    # 36 tamaño (200 m² cae en el escalón de 150+, no en el de 100) + 35 plazo + 20 tipo.
    assert l.score_calif == 91
    assert leads.desglose_score(l) == {"tamano": 36, "plazo": 35, "tipo": 20,
                                       "total": 91, "completo": True}


def test_tipo_de_propiedad_fuera_de_catalogo_no_se_guarda():
    l = EmaLead(phone="3")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "bodega", "tiempo_renta": "12+"})
    assert l.tipo_propiedad is None
    assert "tipo de propiedad" in leads.falta_del_cuestionario(l)


def test_un_valor_malo_no_pisa_uno_bueno_ya_capturado():
    """Descartar es mejor que sobreescribir: lo ya bien capturado se queda como está."""
    l = EmaLead(phone="4", tipo_propiedad="casa", recamaras=3, tiempo_renta="12+")
    leads.apply_capturar_lead(l, {"tiempo_renta": "como un añito"})
    assert l.tiempo_renta == "12+"


def test_los_campos_libres_no_se_validan():
    """Solo los campos con enum tienen catálogo; `resumen` y `zona` son texto libre."""
    l = EmaLead(phone="5")
    leads.apply_capturar_lead(l, {"zona": "San Pedro Garza García",
                                  "resumen": "quiere amueblar oficinas nuevas"})
    assert l.zona == "San Pedro Garza García"
    assert l.resumen == "quiere amueblar oficinas nuevas"


# ─────────── B-3: `presupuesto` era código muerto con aspecto de funcionalidad ───────────

def test_presupuesto_no_lo_llena_el_bot():
    """No está en el esquema, así que el modelo no puede mandarlo. Que tampoco lo finja el
    guardado: la columna se queda para el asesor, pero el bot no la toca."""
    assert "presupuesto" not in CAPTURAR_LEAD_TOOL["function"]["parameters"]["properties"]
    l = EmaLead(phone="6")
    leads.apply_capturar_lead(l, {"presupuesto": "50 mil al mes"})
    assert l.presupuesto is None


def test_uso_si_se_captura_porque_lo_lee_el_cuadrante():
    """`uso` NO es código muerto: es una de las dos variables del cuadrante 2×2
    (`clasificacion.perfil_de`, junto con `ticket_mensual`) y se edita desde el panel."""
    l = EmaLead(phone="7")
    leads.apply_capturar_lead(l, {"uso": "reventa"})
    assert l.uso == "reventa"
