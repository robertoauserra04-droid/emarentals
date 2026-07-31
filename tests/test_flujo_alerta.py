"""Integración del corazón: mensaje entra → el bot clasifica → BUEN LEAD dispara alerta al
admin UNA sola vez y el bot cede la conversación al humano. Con la IA y el envío mockeados.
"""
import app.services.bot.handler as handler
from app.models.lead import AppSetting, EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection


def _sembrar_conversacion(db, phone, texto):
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.add(ChatMessage(phone=phone, direction=MessageDirection.inbound, body=texto))
    db.add(AppSetting(key="bot_enabled", value="true"))
    db.commit()


def test_buen_lead_dispara_alerta_y_cede(db, monkeypatch):
    phone = "5218110000001"
    _sembrar_conversacion(db, phone, "Hola, quiero amueblar una oficina completa por 12 meses en Monterrey")

    # La IA "llama" capturar_lead con un buen lead y responde texto.
    def fake_generate_reply(system, history, handlers):
        handlers["capturar_lead"]({
            "segmento": "corporativo", "necesidad": "oficina_completa",
            "plazo_meses": 12, "zona": "Monterrey", "estado": "calificado",
            "resumen": "Oficina completa 12 meses en Monterrey", "nivel_interes": "Alto",
        })
        return "Con gusto. Le paso con un asesor que le prepara la propuesta."
    monkeypatch.setattr(handler.ai, "generate_reply", fake_generate_reply)

    # No mandar nada real por WhatsApp.
    enviados = []
    monkeypatch.setattr(handler, "settings", handler.settings)
    handler.settings.openai_api_key = "test-key"
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: "wamid-out")

    # Capturar la alerta al admin.
    alertas = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda lead: alertas.append(lead.phone) or True)

    handler.handle_inbound(db, phone, "quiero amueblar una oficina completa por 12 meses", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "calificado"          # buen lead
    assert lead.alertado_at is not None          # se alertó
    assert alertas == [phone]                     # exactamente una alerta
    assert lead.bot_active is False               # el bot cedió al humano
    conv = db.query(Conversation).filter(Conversation.phone == phone).first()
    assert conv.bot_active is False


def test_curioso_no_alerta(db, monkeypatch):
    phone = "5218110000002"
    _sembrar_conversacion(db, phone, "cuanto cuesta un refri?")

    def fake_generate_reply(system, history, handlers):
        # El LLM intenta calificar, pero sin necesidad/plazo reales.
        handlers["capturar_lead"]({"estado": "calificado", "presupuesto": "no sé", "nivel_interes": "Medio"})
        return "Con gusto le ayudo. ¿Para qué espacio sería?"
    monkeypatch.setattr(handler.ai, "generate_reply", fake_generate_reply)
    handler.settings.openai_api_key = "test-key"
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: "wamid-out")
    alertas = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda lead: alertas.append(lead.phone) or True)

    handler.handle_inbound(db, phone, "cuanto cuesta un refri?", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "interesado"    # degradado por la guarda
    assert lead.alertado_at is None        # NO se alertó
    assert alertas == []
    assert lead.bot_active is True         # el bot sigue atendiendo
