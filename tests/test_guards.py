"""fact_guard (anti-cifra inventada) + calificación determinista."""
from app.models.lead import EmaLead
from app.services.bot.guards import fact_guard
from app.services.bot.leads import apply_capturar_lead


def test_fact_guard_bloquea_cifra_inventada():
    draft = "Ese departamento queda en $2,300 al mes, un precio excelente."
    texto, blocked = fact_guard(draft, "system sin cifras", "hola busco rentar")
    assert blocked is True
    assert "$2,300" not in texto   # se deflectó


def test_fact_guard_deja_pasar_cifra_respaldada():
    draft = "El plan es de $1,500 como le comenté."
    texto, blocked = fact_guard(draft, "el plan cuesta $1,500")
    assert blocked is False
    assert texto == draft


def test_fact_guard_no_toca_numeros_chicos():
    draft = "Son 3 recámaras y 2 baños."
    texto, blocked = fact_guard(draft, "system")
    assert blocked is False
    assert texto == draft


def test_apply_no_sobrecalifica_sin_datos():
    lead = EmaLead(phone="52111", estado="nuevo")
    apply_capturar_lead(lead, {"nivel_interes": "Alto"})
    # sin tipo de propiedad ni tiempo → no llega a calificado
    assert lead.estado in ("nuevo", "interesado")
    assert lead.es_buen_prospecto is False


def test_apply_casa_va_a_residencial_bueno():
    lead = EmaLead(phone="52111", estado="interesado")
    apply_capturar_lead(lead, {"tipo_propiedad": "casa", "tiempo_renta": "0-6"})
    assert lead.estado == "residencial_bueno"   # casa siempre es buen prospecto
