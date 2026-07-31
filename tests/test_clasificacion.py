"""Perfil estratégico (cuadrante 2×2) + horas estimadas."""
from app.models.lead import EmaLead
from app.services import clasificacion


def _lead(**kw):
    return EmaLead(phone="x", **kw)


def test_socio_estrategico():
    l = _lead(ticket_mensual=30000, uso="reventa")
    assert clasificacion.compute_perfil(l, 20000) == "socio_estrategico"


def test_aliado_operativo():
    l = _lead(ticket_mensual=10000, uso="reventa")
    assert clasificacion.compute_perfil(l, 20000) == "aliado_operativo"


def test_cliente_premium():
    l = _lead(ticket_mensual=30000, uso="propio")
    assert clasificacion.compute_perfil(l, 20000) == "cliente_premium"


def test_cliente_estandar():
    l = _lead(ticket_mensual=8000, uso="propio")
    assert clasificacion.compute_perfil(l, 20000) == "cliente_estandar"


def test_sin_clasificar_sin_datos():
    assert clasificacion.compute_perfil(_lead(), 20000) == "sin_clasificar"
    assert clasificacion.compute_perfil(_lead(ticket_mensual=5000), 20000) == "sin_clasificar"


def test_umbral_mueve_el_perfil():
    l = _lead(ticket_mensual=15000, uso="reventa")
    assert clasificacion.compute_perfil(l, 20000) == "aliado_operativo"  # bajo el umbral
    assert clasificacion.compute_perfil(l, 10000) == "socio_estrategico"  # sube el umbral -> alto


def test_horas_base_mas_escala_mas_urgencia():
    l = _lead(ticket_mensual=30000, uso="reventa", potencial_escala="7-15", urgencia_cierre="ya")
    # socio (base 5) + escala 7-15 (+2) + urgencia ya (+1) = 8
    assert clasificacion.compute_horas(l, "socio_estrategico") == 8


def test_horas_sin_clasificar_cero():
    assert clasificacion.compute_horas(_lead(), "sin_clasificar") == 0
