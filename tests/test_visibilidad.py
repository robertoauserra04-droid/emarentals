"""Ocultar leads y marcar contactos que no son leads.

Hasta ahora NINGUNA vista filtraba nada: Kanban, Bandeja, Métricas y Resumen traían todos los
leads de la tabla. Estos tests fijan que los cuatro filtren, que es justo el hueco que tiene
aseguradora (ahí `archivado` no toca las métricas).
"""
import app.services.bot.handler as handler
from app.models.lead import AppSetting, ContactoNoLead, EmaLead
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.services import visibilidad


def _lead(db, phone, **kw):
    l = EmaLead(phone=phone, estado=kw.pop("estado", "interesado"), **kw)
    db.add(l)
    db.commit()
    return l


# ─────────── El filtro compartido ───────────

def test_oculto_sale_del_filtro(db):
    visible = _lead(db, "52100")
    _lead(db, "52101", oculto=True)
    assert {l.phone for l in visibilidad.leads_visibles(db)} == {visible.phone}


def test_null_cuenta_como_visible(db):
    """Tras el ADD COLUMN las filas viejas quedan en NULL: `is_(False)` no las capturaría."""
    l = _lead(db, "52102")
    l.oculto = None
    db.commit()
    assert {x.phone for x in visibilidad.leads_visibles(db)} == {"52102"}


def test_no_lead_sale_del_filtro(db):
    _lead(db, "52103")
    db.add(ContactoNoLead(telefono="52103", motivo="proveedor"))
    db.commit()
    assert visibilidad.leads_visibles(db).all() == []
    assert visibilidad.es_no_lead(db, "52103") is True


def test_no_lead_sobrevive_al_reinicio_del_lead(db):
    """Es por teléfono y no una bandera del lead, a propósito: en aseguradora la marca es por
    conversación y el contacto reaparece al abrirse un caso nuevo."""
    l = _lead(db, "52104")
    db.add(ContactoNoLead(telefono="52104"))
    db.commit()
    # Simula el /reiniciar: limpia el lead por completo.
    l.estado, l.oculto, l.score_calif = "nuevo", False, 0
    db.commit()
    assert visibilidad.es_no_lead(db, "52104") is True
    assert visibilidad.leads_visibles(db).all() == []


# ─────────── Las cuatro vistas ───────────

def _pipeline(db, user):
    from app.routers.leads import pipeline
    return pipeline(db=db, user=user)


def test_kanban_no_muestra_ocultos(db, usuario):
    _lead(db, "52110", estado="interesado")
    _lead(db, "52111", estado="interesado", oculto=True)
    d = _pipeline(db, usuario)
    telefonos = {l["phone"] for col in d["leads"].values() for l in col}
    assert telefonos == {"52110"}


def test_metricas_no_cuentan_ocultos_ni_no_leads(db, usuario):
    from app.routers.metrics import metricas
    _lead(db, "52120")
    _lead(db, "52121", oculto=True)
    _lead(db, "52122")
    db.add(ContactoNoLead(telefono="52122"))
    db.commit()
    d = metricas(desde=None, hasta=None, db=db, user=usuario)
    assert d["total"] == 1


def test_resumen_no_cuenta_ocultos(db, usuario):
    from app.routers.leads import resumen_cuadrante
    _lead(db, "52130")
    _lead(db, "52131", oculto=True)
    d = resumen_cuadrante(db=db, user=usuario)
    assert d["total"] == 1


def test_bandeja_no_muestra_ocultos_ni_no_leads(db, usuario):
    from app.routers.conversaciones import listar
    for p in ("52140", "52141", "52142"):
        db.add(Conversation(phone=p, channel="whatsapp"))
    _lead(db, "52140")
    _lead(db, "52141", oculto=True)
    _lead(db, "52142")
    db.add(ContactoNoLead(telefono="52142"))
    db.commit()
    assert {c["phone"] for c in listar(db=db, user=usuario)} == {"52140"}


# ─────────── El bot ignora a los no-leads ───────────

def test_el_bot_no_contesta_a_un_no_lead(db, monkeypatch):
    phone = "5218110000030"
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.add(AppSetting(key="bot_enabled", value="true"))
    db.add(ContactoNoLead(telefono=phone, motivo="proveedor"))
    db.commit()

    llamadas = []
    monkeypatch.setattr(handler.ai, "generate_reply",
                        lambda *a, **k: llamadas.append(1) or "hola")
    handler.settings.openai_api_key = "test-key"

    handler.handle_inbound(db, phone, "hola, les traigo una cotización", channel="whatsapp")

    assert llamadas == []          # la IA ni se llamó
    # …pero el mensaje entrante SÍ quedó guardado: se ve en Historial.
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead is not None
    assert lead.message_count == 1


# ─────────── Historial ───────────

def test_historial_incluye_ocultos_y_no_leads(db, usuario):
    from app.routers.leads import historial
    _lead(db, "52150")
    _lead(db, "52151", oculto=True)
    _lead(db, "52152")
    db.add(ContactoNoLead(telefono="52152"))
    db.commit()

    d = historial(db=db, user=usuario)
    assert d["total"] == 3
    por_tel = {c["phone"]: c for c in d["contactos"]}
    assert por_tel["52151"]["oculto"] is True
    assert por_tel["52152"]["no_lead"] is True
    assert por_tel["52150"]["oculto"] is False


def test_historial_filtra_y_busca(db, usuario):
    from app.routers.leads import historial
    _lead(db, "52160", name="Mariana")
    _lead(db, "52161", name="Pedro", oculto=True)

    assert historial(filtro="ocultos", db=db, user=usuario)["total"] == 1
    assert historial(filtro="activos", db=db, user=usuario)["total"] == 1
    assert historial(search="Mariana", db=db, user=usuario)["total"] == 1
    assert historial(search="5216", db=db, user=usuario)["total"] == 2


def test_historial_sin_no_leads_no_truena(db, usuario):
    """El filtro no_lead con la tabla vacía no debe reventar la query."""
    from app.routers.leads import historial
    _lead(db, "52170")
    assert historial(filtro="no_lead", db=db, user=usuario)["total"] == 0


# ─────────── Ocultar / devolver ───────────

def test_ocultar_y_devolver(db, usuario):
    from app.routers.leads import mostrar, ocultar
    l = _lead(db, "52180")
    ocultar(l.id, db=db, user=usuario)
    assert l.oculto is True and l.oculto_at is not None
    mostrar(l.id, db=db, user=usuario)
    assert l.oculto is False and l.oculto_at is None


def test_marcar_no_lead_tambien_lo_oculta(db, usuario):
    from app.routers.leads import NoLeadIn, marcar_no_lead
    l = _lead(db, "528110000099")
    marcar_no_lead(NoLeadIn(telefono="528110000099", motivo="proveedor"), db=db, user=usuario)
    assert l.oculto is True
    assert visibilidad.es_no_lead(db, "528110000099") is True


def test_desmarcar_no_lead_no_lo_devuelve_solo(db, usuario):
    """Son dos decisiones separadas: dejar de ignorarlo no implica regresarlo al tablero."""
    from app.routers.leads import NoLeadIn, desmarcar_no_lead, marcar_no_lead
    l = _lead(db, "528110000098")
    marcar_no_lead(NoLeadIn(telefono="528110000098"), db=db, user=usuario)
    desmarcar_no_lead("528110000098", db=db, user=usuario)
    assert visibilidad.es_no_lead(db, "528110000098") is False
    assert l.oculto is True
