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
