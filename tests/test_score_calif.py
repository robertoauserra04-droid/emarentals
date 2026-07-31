"""Calificación del bot (flujo por tipo de propiedad) + buen prospecto determinista."""
from app.models.lead import EmaLead
from app.services.bot import leads


def test_casa_siempre_buen_prospecto():
    l = EmaLead(phone="1", tipo_propiedad="casa", recamaras=1, tiempo_renta="0-6")
    bueno, tipo_of = leads.evaluar_prospecto(l)
    assert bueno is True and tipo_of is None


def test_departamento_2_recamaras_bueno():
    l = EmaLead(phone="2", tipo_propiedad="departamento", recamaras=2, tiempo_renta="12+")
    assert leads.evaluar_prospecto(l)[0] is True


def test_departamento_1_recamara_no_bueno():
    l = EmaLead(phone="3", tipo_propiedad="departamento", recamaras=1, tiempo_renta="12+")
    assert leads.evaluar_prospecto(l)[0] is False


def test_oficina_por_m2():
    l = EmaLead(phone="4", tipo_propiedad="oficina", oficina_m2=120, oficina_personas=5, tiempo_renta="6-12")
    bueno, tipo_of = leads.evaluar_prospecto(l)
    assert bueno is True and tipo_of == "tipo1"   # 0-12 meses = Tipo 1


def test_oficina_por_personas_tipo2():
    l = EmaLead(phone="5", tipo_propiedad="oficina", oficina_m2=40, oficina_personas=25, tiempo_renta="12+")
    bueno, tipo_of = leads.evaluar_prospecto(l)
    assert bueno is True and tipo_of == "tipo2"   # 12+ meses = Tipo 2


def test_oficina_chica_no_bueno():
    l = EmaLead(phone="6", tipo_propiedad="oficina", oficina_m2=50, oficina_personas=8, tiempo_renta="0-6")
    assert leads.evaluar_prospecto(l)[0] is False


# ─────────── apply_capturar_lead decide la etapa ───────────

def test_apply_departamento_bueno_queda_calificado():
    l = EmaLead(phone="7", estado="nuevo")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "departamento", "recamaras": 2, "tiempo_renta": "12+"})
    assert l.estado == "calificado"
    assert l.es_buen_prospecto is True
    assert l.marca == "rentals"


def test_apply_oficina_grande_calificado_tipo2():
    l = EmaLead(phone="8", estado="nuevo")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "oficina", "oficina_personas": 30, "tiempo_renta": "12+"})
    assert l.estado == "calificado"
    assert l.tipo_oficina == "tipo2"
    assert l.marca == "office"


def test_apply_departamento_chico_queda_interesado():
    l = EmaLead(phone="9", estado="nuevo")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "departamento", "recamaras": 1, "tiempo_renta": "6-12"})
    assert l.estado == "interesado"     # completo pero no buen prospecto
    assert l.es_buen_prospecto is False


def test_apply_incompleto_no_califica():
    l = EmaLead(phone="10", estado="nuevo")
    leads.apply_capturar_lead(l, {"tipo_propiedad": "oficina"})   # falta m²/personas y tiempo
    assert l.estado == "interesado"     # entró al pipeline pero sin calificar
    assert l.es_buen_prospecto is False


def test_apply_motivo_perdida_marca_perdido():
    l = EmaLead(phone="11", estado="interesado")
    leads.apply_capturar_lead(l, {"motivo_perdida": "solo comparaba precios"})
    assert l.estado == "perdido"
