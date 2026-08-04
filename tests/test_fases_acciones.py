"""La fase manda: qué se notifica, a quién y con qué mensaje de cierre.

Antes esto estaba hardcodeado dentro del bot (`if actions["alertar"] or lead.es_buen_prospecto`)
y no se podía configurar sin tocar código.
"""
import app.services.bot.handler as handler
from app.models.lead import AppSetting, ContactoAlerta, EmaLead, Fase
from app.models.messaging import ChatMessage, Conversation, MessageDirection
from app.routers import fases as fases_router
from app.services import fases_acciones


def _fase(db, clave):
    fases_router.seed_fases(db)
    return db.query(Fase).filter(Fase.clave == clave).first()


def _sembrar(db, phone):
    db.add(Conversation(phone=phone, channel="whatsapp"))
    db.add(ChatMessage(phone=phone, direction=MessageDirection.inbound, body="hola"))
    db.add(AppSetting(key="bot_enabled", value="true"))
    db.commit()


def _mockear(db, monkeypatch, captura):
    def fake(system, history, handlers):
        handlers["capturar_lead"](captura)
        return "Con gusto."
    monkeypatch.setattr(handler.ai, "generate_reply", fake)
    handler.settings.openai_api_key = "test-key"
    enviados = []
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: enviados.append(b) or "wamid")
    destinos = []
    import app.services.kapso as kapso
    monkeypatch.setattr(kapso, "send_template_vars_sync", lambda tel, tpl, v: destinos.append(tel) or "ok")
    return enviados, destinos


# ─────────── Toggle de notificación ───────────

def test_toggle_apagado_no_notifica(db, monkeypatch):
    f = _fase(db, "residencial_bueno")
    f.notificar = False
    db.commit()

    lead = EmaLead(phone="52001", estado="residencial_bueno")
    db.add(lead)
    db.commit()
    avisos = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda l, **kw: avisos.append(l.phone) or True)

    fases_acciones.al_entrar_a_fase(db, lead, enviar=False)
    assert avisos == []
    assert lead.alertado_at is None


def test_toggle_encendido_notifica(db, monkeypatch):
    f = _fase(db, "residencial_baja")
    f.notificar = True          # el admin decide avisar también de los de baja prioridad
    db.commit()

    lead = EmaLead(phone="52002", estado="residencial_baja")
    db.add(lead)
    db.commit()
    avisos = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda l, **kw: avisos.append(l.phone) or True)

    fases_acciones.al_entrar_a_fase(db, lead, enviar=False)
    assert avisos == ["52002"]
    assert lead.alertado_at is not None


def test_incompleto_notifica_aunque_el_toggle_este_apagado(db, monkeypatch):
    """Pidió un humano a medias: acabamos de apagar el bot, si no avisamos nadie lo atiende."""
    f = _fase(db, "interesado_residencial")
    f.notificar = False
    db.commit()

    lead = EmaLead(phone="52003", estado="interesado_residencial")
    db.add(lead)
    db.commit()
    avisos = []
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda l, **kw: avisos.append(kw.get("incompleto")) or True)

    fases_acciones.al_entrar_a_fase(db, lead, incompleto=True, enviar=False)
    assert avisos == [True]


# ─────────── Destinatarios: lista GLOBAL, la misma para todas las fases ───────────

def test_avisa_a_todo_el_directorio(db):
    fases_router.seed_fases(db)
    db.add_all([ContactoAlerta(nombre="Ana", telefono="528110000001"),
                ContactoAlerta(nombre="Beto", telefono="528110000002")])
    db.commit()

    assert fases_router.telefonos_alerta(db) == ["528110000001", "528110000002"]


def test_directorio_vacio_cae_al_env(db, monkeypatch):
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti.settings, "alerta_admin_telefonos", "528119999999")

    assert noti._destinos(db) == ["528119999999"]


def test_contacto_inactivo_no_recibe(db):
    c = ContactoAlerta(nombre="Ana", telefono="528110000003")
    db.add(c)
    db.commit()
    assert fases_router.telefonos_alerta(db) == ["528110000003"]

    c.activo = False
    db.commit()
    assert fases_router.telefonos_alerta(db) == []


def test_contacto_borrado_deja_de_recibir(db):
    c = ContactoAlerta(nombre="Ana", telefono="528110000004")
    db.add(c)
    db.commit()
    db.delete(c)
    db.commit()
    assert fases_router.telefonos_alerta(db) == []


def test_el_aviso_sale_a_todos_los_del_directorio(db, monkeypatch):
    """La fase decide SI se avisa; a quién es siempre el directorio completo."""
    f = _fase(db, "residencial_bueno")
    f.notificar = True
    db.add_all([ContactoAlerta(nombre="Ana", telefono="528110000001"),
                ContactoAlerta(nombre="Beto", telefono="528110000002")])
    db.commit()
    lead = EmaLead(phone="52099", estado="residencial_bueno")
    db.add(lead)
    db.commit()

    destinos = []
    import app.services.kapso as kapso
    monkeypatch.setattr(kapso, "send_template_vars_sync",
                        lambda tel, tpl, v: destinos.append(tel) or "ok")
    monkeypatch.setattr(kapso, "send_text_sync", lambda tel, txt: "ok")

    fases_acciones.al_entrar_a_fase(db, lead, enviar=False)
    assert destinos == ["528110000001", "528110000002"]


# ─────────── Mensaje de cierre ───────────

def test_mensaje_de_cierre_se_manda_una_sola_vez(db, monkeypatch):
    f = _fase(db, "residencial_baja")
    f.mensaje_cierre = "No cumple con nuestros criterios de renta."
    db.commit()
    lead = EmaLead(phone="52004", estado="residencial_baja")
    db.add(lead)
    db.commit()

    enviados = []
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: enviados.append(b) or "wamid")

    fases_acciones.al_entrar_a_fase(db, lead)
    fases_acciones.al_entrar_a_fase(db, lead)      # segundo turno: no debe repetirlo
    assert enviados == ["No cumple con nuestros criterios de renta."]
    assert lead.cierre_enviado_en == "residencial_baja"


def test_sin_mensaje_de_cierre_no_manda_nada(db, monkeypatch):
    f = _fase(db, "nuevo")
    f.mensaje_cierre = None
    db.commit()
    lead = EmaLead(phone="52005", estado="nuevo")
    db.add(lead)
    db.commit()

    enviados = []
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: enviados.append(b) or "wamid")

    fases_acciones.al_entrar_a_fase(db, lead)
    assert enviados == []


def test_cierre_no_se_manda_si_el_cuestionario_quedo_a_medias(db, monkeypatch):
    """En 'Interesado' el lead está de paso, no clasificado ahí: no le mandamos un cierre."""
    f = _fase(db, "interesado_residencial")
    f.mensaje_cierre = "Gracias, un asesor le contactará."
    db.commit()
    lead = EmaLead(phone="52006", estado="interesado_residencial")
    db.add(lead)
    db.commit()

    enviados = []
    import app.services.messaging_out as mo
    monkeypatch.setattr(mo, "_enviar_por_canal", lambda p, b, c: enviados.append(b) or "wamid")
    import app.services.notificaciones as noti
    monkeypatch.setattr(noti, "alertar_admin", lambda l, **kw: True)

    fases_acciones.al_entrar_a_fase(db, lead, incompleto=True)
    assert enviados == []


# ─────────── Integración por el bot ───────────

def test_lead_completo_recibe_el_cierre_de_su_fase(db, monkeypatch):
    phone = "5218110000020"
    _sembrar(db, phone)
    fases_router.seed_fases(db)
    f = db.query(Fase).filter(Fase.clave == "residencial_bueno").first()
    f.mensaje_cierre = "Perfecto, un asesor se pondrá en contacto con usted."
    f.notificar = False          # aislamos: solo queremos ver el mensaje de cierre
    db.commit()

    enviados, _ = _mockear(db, monkeypatch, {"tipo_propiedad": "casa", "recamaras": 3,
                                             "tiempo_renta": "12+"})
    handler.handle_inbound(db, phone, "una casa de 3 recámaras", channel="whatsapp")

    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    assert lead.estado == "residencial_bueno"
    assert "Perfecto, un asesor se pondrá en contacto con usted." in enviados


# ─────────── Recuperación: la selección la manda el toggle ───────────

def test_elegibles_usa_las_fases_marcadas(db):
    from app.services import recovery
    fases_router.seed_fases(db)
    for f in db.query(Fase).all():
        f.recuperar = (f.clave == "residencial_baja")
    db.commit()

    db.add(EmaLead(phone="52010", estado="residencial_baja"))
    db.add(EmaLead(phone="52011", estado="interesado_residencial"))
    db.commit()

    telefonos = {l.phone for l in recovery._elegibles(db)}
    assert telefonos == {"52010"}


def test_elegibles_vacio_si_ninguna_fase_recupera(db):
    from app.services import recovery
    fases_router.seed_fases(db)
    for f in db.query(Fase).all():
        f.recuperar = False
    db.commit()
    db.add(EmaLead(phone="52012", estado="interesado_residencial"))
    db.commit()

    assert recovery._elegibles(db) == []


# ─────────── El popover del score no puede desincronizarse ───────────

def test_score_info_sale_de_las_mismas_constantes():
    from app.services.bot import leads
    info = fases_router.score_info(user=None)
    assert info["plazo"]["tabla"] is leads.TABLA_PLAZO
    assert info["tipo"]["tabla"] is leads.TABLA_TIPO
    assert info["tamano"]["total"] == 45
    assert len(info["tamano"]["recamaras"]) == len(leads.TABLA_TAMANO_RECAMARAS)


# ─────────── Lo que necesita el panel de la ⓘ del Kanban ───────────

def test_pipeline_expone_lo_que_edita_la_i(db, usuario):
    """Criterios y mensaje de cierre se editan desde la ⓘ del Kanban, no desde Fases."""
    from app.routers.leads import pipeline
    fases_router.seed_fases(db)
    d = pipeline(db=db, user=usuario)
    meta = d["fases"]["residencial_bueno"]
    assert meta["id"] and meta["nombre"] and meta["criterios"]
    assert "asesor" in meta["mensaje_cierre"]


def test_guardar_desde_la_i_persiste(db, usuario):
    from app.routers.fases import FaseIn, editar
    f = _fase(db, "oficina_mid")
    editar(f.id, FaseIn(nombre=f.nombre, criterios="Nuevo criterio",
                        mensaje_cierre="Nuevo mensaje"), db=db, user=usuario)
    db.refresh(f)
    assert f.criterios == "Nuevo criterio" and f.mensaje_cierre == "Nuevo mensaje"


def test_vaciar_el_mensaje_de_cierre_lo_desactiva(db, usuario):
    from app.routers.fases import FaseIn, editar
    f = _fase(db, "residencial_bueno")
    editar(f.id, FaseIn(nombre=f.nombre, mensaje_cierre="  "), db=db, user=usuario)
    db.refresh(f)
    assert f.mensaje_cierre is None
