"""El cuestionario manda: nada se clasifica, notifica ni apaga el bot hasta terminarlo.

Cubre el bug de origen — con solo decir "es para una casa" el lead quedaba `es_buen_prospecto`,
se avisaba al admin y se apagaba el bot, antes de la PREGUNTA 2.
"""
import app.services.bot.handler as handler
from app.models.lead import AppSetting, EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.services.bot import leads


# ─────────── cuestionario_completo / falta_del_cuestionario ───────────

def test_casa_sin_recamaras_no_esta_completa():
    """Casa exige recámaras igual que departamento: el prompt siempre las preguntó."""
    l = EmaLead(phone="1", tipo_propiedad="casa", tiempo_renta="12+")
    assert leads.cuestionario_completo(l) is False
    assert "recámaras" in leads.falta_del_cuestionario(l)


def test_casa_completa_con_recamaras():
    l = EmaLead(phone="2", tipo_propiedad="casa", recamaras=3, tiempo_renta="12+")
    assert leads.cuestionario_completo(l) is True
    assert leads.falta_del_cuestionario(l) == []


def test_oficina_necesita_m2_o_personas():
    l = EmaLead(phone="3", tipo_propiedad="oficina", tiempo_renta="12+")
    assert leads.cuestionario_completo(l) is False
    l.oficina_personas = 25
    assert leads.cuestionario_completo(l) is True


def test_falta_tiempo_de_renta():
    l = EmaLead(phone="4", tipo_propiedad="departamento", recamaras=2)
    assert leads.falta_del_cuestionario(l) == ["tiempo de renta"]


def test_lead_vacio_falta_todo():
    l = EmaLead(phone="5")
    assert leads.falta_del_cuestionario(l) == ["tipo de propiedad", "tiempo de renta"]


# ─────────── Score de prioridad ───────────

def _score(**kw):
    l = EmaLead(phone="s", **kw)
    return leads.recompute_score_calif(l)


def test_score_incompleto_es_cero():
    assert _score(tipo_propiedad="casa", tiempo_renta="12+") == 0      # sin recámaras


def test_score_maximo():
    assert _score(tipo_propiedad="oficina", oficina_m2=300, oficina_personas=50,
                  tiempo_renta="12+") == 100                            # 45 + 35 + 20


def test_score_oficina_en_el_umbral():
    assert _score(tipo_propiedad="oficina", oficina_m2=100, oficina_personas=20,
                  tiempo_renta="12+") == 83                             # 28 + 35 + 20


def test_score_depto_dos_recamaras():
    assert _score(tipo_propiedad="departamento", recamaras=2, tiempo_renta="12+") == 73


def test_score_casa_chica_plazo_corto():
    assert _score(tipo_propiedad="casa", recamaras=1, tiempo_renta="0-6") == 35   # 12 + 7 + 16


def test_score_separa_lo_que_antes_empataba():
    """El problema del score viejo: una oficina enorme y un depto de 2 recámaras daban 95 los dos."""
    grande = _score(tipo_propiedad="oficina", oficina_m2=800, oficina_personas=80, tiempo_renta="12+")
    chico = _score(tipo_propiedad="departamento", recamaras=2, tiempo_renta="12+")
    assert grande > chico


def test_score_bajo_no_impide_ser_buen_prospecto():
    """La fase la manda la regla de EMA, no el score: pueden discrepar a propósito."""
    l = EmaLead(phone="6", estado="nuevo")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "casa", "recamaras": 1, "tiempo_renta": "0-6"})
    assert l.estado == "residencial_normal"   # cumple el umbral pero renta corta
    assert l.es_buen_prospecto is True
    assert l.score_calif == 35


def test_desglose_suma_el_total():
    l = EmaLead(phone="7", tipo_propiedad="oficina", oficina_personas=25, tiempo_renta="6-12")
    d = leads.desglose_score(l)
    assert d["completo"] is True
    assert d["tamano"] + d["plazo"] + d["tipo"] == d["total"]


# ─────────── Integración: el handler no escala antes de tiempo ───────────

def _sembrar(db, phone):
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.add(ChatMessage(phone=phone, direction=MessageDirection.inbound, body="hola"))
    db.add(AppSetting(key="bot_enabled", value="true"))
    db.commit()


def _mockear(monkeypatch, captura=None, alertar=False):
    """Mockea la IA para que llame las tools que le indiquemos, y captura las alertas."""
    def fake(system, history, handlers):
        if captura is not None:
            handlers["capturar_lead"](captura)
        if alertar:
            handlers["alertar_asesor"]({"motivo": "quiere hablar con alguien"})
        return "Con gusto."
    monkeypatch.setattr(handler.ai, "generate_reply", fake)
    handler.settings.openai_api_key = "test-key"
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: "wamid")
    alertas = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin",
                        lambda lead, **kw: alertas.append(kw.get("incompleto", False)) or True)
    return alertas


def test_casa_sin_recamaras_no_dispara_nada(db, monkeypatch):
    """El bug exacto: 'quiero rentar para una casa' NO debe cerrar el flujo."""
    phone = "5218110000010"
    _sembrar(db, phone)
    alertas = _mockear(monkeypatch, captura={"tipo_propiedad": "casa", "tiempo_renta": "12+"})

    handler.handle_inbound(db, phone, "quiero rentar muebles para una casa", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "interesado_residencial"   # antesala, no fase de cierre
    assert lead.es_buen_prospecto is False    # no se clasificó
    assert lead.score_calif == 0
    assert lead.alertado_at is None           # no se notificó
    assert alertas == []
    assert lead.bot_active is True            # el bot sigue preguntando
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    assert conv.bot_active is True


def test_cuestionario_completo_si_escala(db, monkeypatch):
    phone = "5218110000011"
    _sembrar(db, phone)
    alertas = _mockear(monkeypatch, captura={"tipo_propiedad": "casa", "recamaras": 3,
                                             "tiempo_renta": "12+"})

    handler.handle_inbound(db, phone, "una casa de 3 recámaras por 2 años", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "residencial_bueno"
    assert lead.alertado_at is not None
    assert alertas == [False]                 # alerta normal, no marcada como incompleta
    assert lead.bot_active is False


def test_pide_asesor_a_medias_escala_marcado_incompleto(db, monkeypatch):
    """Si pide un humano sin terminar, se avisa SIEMPRE: acabamos de apagar el bot."""
    phone = "5218110000012"
    _sembrar(db, phone)
    alertas = _mockear(monkeypatch, captura={"tipo_propiedad": "oficina"}, alertar=True)

    handler.handle_inbound(db, phone, "quiero hablar con un asesor", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "interesado_oficina"  # antesala de oficina: faltan datos
    assert lead.es_buen_prospecto is False
    assert lead.bot_active is False           # pero sí cedió al humano
    assert alertas == [True]                  # y el aviso va marcado como incompleto


def test_low_priority_completo_no_alerta(db, monkeypatch):
    phone = "5218110000013"
    _sembrar(db, phone)
    alertas = _mockear(monkeypatch, captura={"tipo_propiedad": "departamento", "recamaras": 1,
                                             "tiempo_renta": "0-6"})

    handler.handle_inbound(db, phone, "un depto de 1 recámara por 3 meses", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "residencial_baja"
    assert alertas == []                      # no es buen prospecto → no se avisa
    assert lead.bot_active is False           # pero el cuestionario terminó


# ─────────── B-2: el cuestionario a la inversa (plazo antes que tipo) ───────────
# `falta_del_cuestionario` devolvía ["tipo de propiedad", "tiempo de renta"] en cuanto faltaba el
# tipo, SIN mirar si el plazo ya estaba capturado. Quien abría con "necesito amueblar por un año"
# recibía el prompt que dice "TODAVÍA NO SABES NADA de este prospecto" y el bot le repreguntaba el
# plazo que acababa de dar: un turno perdido y una mala primera impresión.

def test_plazo_capturado_no_se_vuelve_a_pedir():
    """La prueba exacta de B-2."""
    l = EmaLead(phone="b2-1", tiempo_renta="12+")
    falta = leads.falta_del_cuestionario(l)
    assert "tiempo de renta" not in falta
    assert falta == ["tipo de propiedad"]


def test_sin_nada_capturado_se_piden_las_dos():
    l = EmaLead(phone="b2-2")
    assert leads.falta_del_cuestionario(l) == ["tipo de propiedad", "tiempo de renta"]


def test_las_cuatro_combinaciones_de_tipo_y_plazo():
    """Con/sin tipo × con/sin plazo. El riesgo del arreglo era dejar de preguntar algo."""
    sin_nada = EmaLead(phone="b2-3")
    solo_plazo = EmaLead(phone="b2-4", tiempo_renta="6-12")
    solo_tipo = EmaLead(phone="b2-5", tipo_propiedad="casa", recamaras=2)
    completo = EmaLead(phone="b2-6", tipo_propiedad="casa", recamaras=2, tiempo_renta="6-12")

    assert leads.falta_del_cuestionario(sin_nada) == ["tipo de propiedad", "tiempo de renta"]
    assert leads.falta_del_cuestionario(solo_plazo) == ["tipo de propiedad"]
    assert leads.falta_del_cuestionario(solo_tipo) == ["tiempo de renta"]
    assert leads.falta_del_cuestionario(completo) == []
    assert leads.cuestionario_completo(completo) is True


def test_con_solo_el_plazo_el_prompt_ya_no_dice_que_no_sabe_nada():
    """La otra mitad de B-2: el listado se arregló, pero el prompt tenía su propia prueba de
    'no sé nada' (`not lead.tipo_propiedad`) que caía en la misma trampa."""
    from app.services.bot.prompt import build_system_prompt

    p = build_system_prompt(EmaLead(phone="b2-7", tiempo_renta="12+"))
    assert "TODAVÍA NO TIENES NADA CAPTURADO" not in p
    assert "LO QUE YA SABES" in p
    assert "12 meses o más" in p


def test_lead_recien_creado_si_dice_que_no_sabe_nada():
    """Y sin nada capturado, la frase tiene que seguir apareciendo: es la que arranca bien."""
    from app.services.bot.prompt import build_system_prompt

    assert "TODAVÍA NO TIENES NADA CAPTURADO" in build_system_prompt(EmaLead(phone="b2-8"))


def test_el_prompt_manda_capturar_lo_ya_dicho_antes_de_preguntar():
    """B-9: el prospecto que abre con "una casa de 3 recámaras" dentro de una pregunta de precio.

    Medido en vivo (gpt-4o-mini): con la orden solo a media altura del prompt el modelo entendía
    el dato —saltaba la pregunta del tipo— pero NO llamaba `capturar_lead`, así que el lead se
    quedaba sin tipo ni recámaras (0/5). Con la orden repetida AL FINAL: 6/6. Por eso el test
    cuida las dos cosas: que la regla esté, y que esté al final.
    """
    from app.services.bot.prompt import build_system_prompt

    p = build_system_prompt(None, es_primer_contacto=True)
    assert "RELEE EL MENSAJE DEL PROSPECTO Y CAPTURA LO QUE YA TE DIJO" in p
    cola = p[-600:]
    assert "OBLIGATORIO" in cola and "capturar_lead" in cola
