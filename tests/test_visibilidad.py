"""Borrar leads del tablero y marcar los números que el bot debe ignorar.

Dos reglas que estos tests fijan:

1. **Sacar del tablero BORRA** (`purga.borrar_lead`): no hay Historial ni papelera. Si esa persona
   vuelve a escribir, entra otra vez como lead nuevo.
2. **No lead** marca el TELÉFONO para siempre. Como los canales guardan el número crudo
   (`5218110000030`) y la marca se guarda normalizada (`528110000030`), todo se compara por
   variantes — antes no coincidían y marcar "no es lead" a un número mexicano no servía de nada.

Además, ninguna vista (Kanban, Bandeja, Métricas, Resumen) debe contar a los no-leads, que es el
hueco que tiene aseguradora (ahí `archivado` no toca las métricas).
"""
import app.services.bot.handler as handler
from app.models.lead import AppSetting, ContactoNoLead, EmaLead
from app.models.lead_evento import LeadEvento
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.services import purga, visibilidad


def _lead(db, phone, **kw):
    l = EmaLead(phone=phone, estado=kw.pop("estado", "interesado"), **kw)
    db.add(l)
    db.commit()
    return l


def _bot_listo(db, monkeypatch, respuesta="Con gusto. ¿Qué tipo de propiedad desea amueblar?"):
    """Deja el bot en condiciones de contestar, con la IA mockeada. Devuelve la lista de llamadas."""
    if not db.query(AppSetting).filter(AppSetting.key == "bot_enabled").first():
        db.add(AppSetting(key="bot_enabled", value="true"))
        db.commit()
    llamadas = []
    monkeypatch.setattr(handler.ai, "generate_reply",
                        lambda *a, **k: llamadas.append(1) or respuesta)
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: "wamid-out")
    handler.settings.openai_api_key = "test-key"
    return llamadas


# ─────────── El filtro compartido ───────────

def test_no_lead_sale_del_filtro(db):
    _lead(db, "52103")
    db.add(ContactoNoLead(telefono="52103", motivo="proveedor"))
    db.commit()
    assert visibilidad.leads_visibles(db).all() == []
    assert visibilidad.es_no_lead(db, "52103") is True


def test_no_lead_compara_por_variantes(db):
    """El lead se guarda con el 1 de móvil y la marca sin él: aun así deben coincidir."""
    _lead(db, "5218110000030")
    db.add(ContactoNoLead(telefono="528110000030"))
    db.commit()
    assert visibilidad.leads_visibles(db).all() == []
    assert visibilidad.es_no_lead(db, "5218110000030") is True
    assert visibilidad.es_no_lead(db, "528110000030") is True


def test_no_lead_sobrevive_al_borrado_del_lead(db, usuario):
    """Es por teléfono y no una bandera del lead, a propósito: en aseguradora la marca es por
    conversación y el contacto reaparece al abrirse un caso nuevo."""
    from app.routers.leads import NoLeadIn, marcar_no_lead
    _lead(db, "52104")
    marcar_no_lead(NoLeadIn(telefono="52104"), db=db, user=usuario)
    assert db.query(EmaLead).filter(EmaLead.phone == "52104").first() is None
    assert visibilidad.es_no_lead(db, "52104") is True


# ─────────── Las cuatro vistas ───────────

def test_kanban_no_muestra_no_leads(db, usuario):
    from app.routers.leads import pipeline
    _lead(db, "52110", estado="interesado")
    _lead(db, "52111", estado="interesado")
    db.add(ContactoNoLead(telefono="52111"))
    db.commit()
    d = pipeline(db=db, user=usuario)
    assert {l["phone"] for col in d["leads"].values() for l in col} == {"52110"}


def test_metricas_no_cuentan_no_leads(db, usuario):
    from app.routers.metrics import metricas
    _lead(db, "52120")
    _lead(db, "52122")
    db.add(ContactoNoLead(telefono="52122"))
    db.commit()
    assert metricas(desde=None, hasta=None, db=db, user=usuario)["total"] == 1


def test_resumen_no_cuenta_no_leads(db, usuario):
    from app.routers.leads import resumen_cuadrante
    _lead(db, "52130")
    _lead(db, "52131")
    db.add(ContactoNoLead(telefono="52131"))
    db.commit()
    assert resumen_cuadrante(db=db, user=usuario)["total"] == 1


def test_bandeja_no_muestra_no_leads(db, usuario):
    from app.routers.conversaciones import listar
    for p in ("52140", "52142"):
        db.add(Conversation(phone=p, channel="whatsapp"))
    _lead(db, "52140")
    _lead(db, "52142")
    db.add(ContactoNoLead(telefono="52142"))
    db.commit()
    assert {c["phone"] for c in listar(db=db, user=usuario)} == {"52140"}


# ─────────── Borrar del tablero ───────────

def test_borrar_lead_no_deja_rastro(db, usuario):
    from app.routers.leads import borrar
    phone = "5218110000010"
    l = _lead(db, phone)
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.add(ChatMessage(phone=phone, channel="whatsapp",
                       direction=MessageDirection.inbound, body="hola"))
    db.add(LeadEvento(lead_id=l.id, tipo="etapa", detalle="nuevo → interesado"))
    db.commit()

    borrar(l.id, db=db, user=usuario)

    assert db.query(EmaLead).filter(EmaLead.phone == phone).first() is None
    assert db.query(ChatMessage).filter(ChatMessage.phone == phone).count() == 0
    assert db.query(Conversation).filter(Conversation.phone == phone).count() == 0
    assert db.query(LeadEvento).count() == 0


def test_borrado_vuelve_a_escribir_y_entra_como_nuevo(db, monkeypatch, usuario):
    """El efecto buscado del borrado físico: no queda nada que 'recuerde' al contacto."""
    from app.routers.leads import borrar
    phone = "5218110000011"
    llamadas = _bot_listo(db, monkeypatch)

    handler.handle_inbound(db, phone, "hola, quiero rentar muebles", channel="whatsapp")
    l = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    l.estado, l.score_calif, l.bot_active = "residencial_bueno", 80, False
    db.commit()
    borrar(l.id, db=db, user=usuario)

    handler.handle_inbound(db, phone, "hola de nuevo", channel="whatsapp")

    # Fila nueva: SQLite puede reciclar el id, pero nada del lead anterior sobrevive.
    nuevo = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert nuevo is not None
    assert nuevo.estado == "nuevo"
    assert (nuevo.score_calif or 0) == 0
    assert nuevo.bot_active is True
    assert nuevo.message_count == 1
    assert len(llamadas) == 2          # el bot le contestó las dos veces


# ─────────── No lead ───────────

def test_marcar_no_lead_borra_el_lead_y_calla_al_bot(db, usuario, monkeypatch):
    """El caso real: número de WhatsApp MX (con el 1) marcado desde el panel."""
    from app.routers.leads import NoLeadIn, marcar_no_lead
    phone = "5218110000030"
    l = _lead(db, phone)
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.commit()

    r = marcar_no_lead(NoLeadIn(telefono=phone, motivo="proveedor"), db=db, user=usuario)
    assert r["borrado"] is True
    assert db.query(EmaLead).filter(EmaLead.phone == phone).first() is None
    assert visibilidad.es_no_lead(db, phone) is True

    llamadas = _bot_listo(db, monkeypatch)
    handler.handle_inbound(db, phone, "les traigo una cotización", channel="whatsapp")
    assert llamadas == []                       # la IA ni se llamó
    # El lead se vuelve a crear (el webhook registra todo lo que entra) pero no se ve en ningún lado.
    assert visibilidad.leads_visibles(db).all() == []


def test_marcar_no_lead_sin_lead_no_truena(db, usuario):
    from app.routers.leads import NoLeadIn, marcar_no_lead
    r = marcar_no_lead(NoLeadIn(telefono="528110000044"), db=db, user=usuario)
    assert r["borrado"] is False
    assert visibilidad.es_no_lead(db, "528110000044") is True


def test_quitar_de_no_leads_devuelve_al_bot(db, usuario, monkeypatch):
    from app.routers.leads import NoLeadIn, desmarcar_no_lead, marcar_no_lead
    phone = "5218110000031"
    marcar_no_lead(NoLeadIn(telefono=phone), db=db, user=usuario)
    desmarcar_no_lead(phone, db=db, user=usuario)
    assert visibilidad.es_no_lead(db, phone) is False

    llamadas = _bot_listo(db, monkeypatch)
    handler.handle_inbound(db, phone, "hola", channel="whatsapp")
    assert len(llamadas) == 1
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "nuevo"
