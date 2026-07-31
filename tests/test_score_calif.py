"""Score comercial determinista + guarda de BUEN LEAD (ticket/volumen/plazo)."""
from app.models.lead import EmaLead
from app.services.bot import leads


def test_lead_vacio_score_bajo():
    l = EmaLead(phone="1", estado="nuevo")
    assert leads.recompute_score_calif(l) == 0


def test_score_sube_con_datos():
    l = EmaLead(phone="2", estado="nuevo", segmento="corporativo", necesidad="oficina_completa",
                plazo_meses=18, fecha_entrega="ya", zona="Monterrey")
    assert leads.recompute_score_calif(l) >= 70


def test_piso_por_fase():
    l = EmaLead(phone="3", estado="calificado")  # sin datos, pero calificado
    assert leads.recompute_score_calif(l) >= 65
    l.estado = "ganado"
    assert leads.recompute_score_calif(l) == 100


def test_es_distinto_del_score_de_recuperacion():
    l = EmaLead(phone="4", estado="interesado", segmento="oficina", plazo_meses=6)
    leads.recompute_score_calif(l)
    assert l.score_calif > 0
    assert (l.score or 0) == 0


# ─────────── Guarda de BUEN LEAD ───────────

def test_curioso_no_califica():
    """Solo pregunta precio, sin necesidad/plazo → se degrada a interesado, NO alerta."""
    l = EmaLead(phone="5", estado="nuevo")
    args = {"estado": "calificado", "presupuesto": "no sé"}
    assert leads.validar_etapa(l, "calificado", args) == "interesado"


def test_residencial_pieza_suelta_corto_no_califica():
    """Un refri por 3 meses (residencial, pieza suelta, plazo corto) NO es buen lead."""
    l = EmaLead(phone="6", estado="nuevo")
    args = {"estado": "calificado", "segmento": "residencial", "necesidad": "pieza_suelta",
            "plazo_meses": 3, "zona": "Monterrey"}
    assert leads.validar_etapa(l, "calificado", args) == "interesado"


def test_corporativo_califica():
    """Segmento corporativo con necesidad + plazo + zona → BUEN LEAD (calificado)."""
    l = EmaLead(phone="7", estado="interesado")
    args = {"estado": "calificado", "segmento": "corporativo", "necesidad": "oficina_completa",
            "plazo_meses": 12, "zona": "CDMX"}
    assert leads.validar_etapa(l, "calificado", args) == "calificado"


def test_residencial_plazo_largo_califica():
    """Residencial pero a 18 meses (quiere a futuro) + casa completa → buen lead."""
    l = EmaLead(phone="8", estado="interesado")
    args = {"estado": "calificado", "segmento": "residencial", "necesidad": "casa_completa",
            "plazo_meses": 18, "fecha_entrega": "1-4sem"}
    assert leads.validar_etapa(l, "calificado", args) == "calificado"


def test_apply_capturar_lead_marca_calificado():
    """El flujo real: apply_capturar_lead con buen lead deja estado=calificado."""
    l = EmaLead(phone="9", estado="nuevo")
    leads.apply_capturar_lead(l, {"segmento": "airbnb", "necesidad": "paquete",
                                  "plazo_meses": 12, "zona": "Cancún", "estado": "calificado"})
    assert l.estado == "calificado"
    assert l.score_calif >= 65
