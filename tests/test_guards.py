"""fact_guard (anti-cifra inventada) + guarda de etapa determinista."""
from app.models.lead import EmaLead
from app.services.bot.guards import fact_guard
from app.services.bot.leads import validar_etapa, apply_capturar_lead


def test_fact_guard_bloquea_cifra_inventada():
    draft = "Ese paquete queda en $2,300 al mes, un precio excelente."
    texto, blocked = fact_guard(draft, "system sin cifras", "hola busco amueblar")
    assert blocked is True
    assert "$2,300" not in texto   # se deflectó


def test_fact_guard_deja_pasar_cifra_respaldada():
    draft = "El plan es de $1,500 como le comenté."
    texto, blocked = fact_guard(draft, "el plan cuesta $1,500")
    assert blocked is False
    assert texto == draft


def test_fact_guard_no_toca_numeros_chicos():
    draft = "Son 3 sillas y 2 mesas."
    texto, blocked = fact_guard(draft, "system")
    assert blocked is False
    assert texto == draft


def test_guarda_etapa_degrada_calificado_sin_datos():
    lead = EmaLead(phone="52111", estado="interesado")
    # el LLM quiere marcar buen lead pero no hay necesidad/plazo
    assert validar_etapa(lead, "calificado", {}) == "interesado"


def test_guarda_etapa_permite_calificado_con_buen_lead():
    lead = EmaLead(phone="52111", estado="interesado")
    args = {"segmento": "corporativo", "necesidad": "oficina_completa",
            "plazo_meses": 12, "zona": "Monterrey"}
    assert validar_etapa(lead, "calificado", args) == "calificado"


def test_apply_capturar_lead_no_sobrecalifica():
    lead = EmaLead(phone="52111", estado="nuevo")
    apply_capturar_lead(lead, {"estado": "calificado", "nivel_interes": "Alto"})
    # sin necesidad/plazo → se queda en interesado, NO calificado
    assert lead.estado == "interesado"
