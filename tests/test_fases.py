"""Fases configurables: seed, borrar cualquier fase, y ruteo con fallback (resolver_clave)."""
from app.models.lead import EmaLead, Fase
from app.routers import fases as F


def test_seed_crea_las_base(db):
    F.seed_fases(db)
    claves = [f.clave for f in F.listar_fases(db)]
    assert claves == ["nuevo", "interesado", "low_priority", "residencial_bueno",
                      "oficina_bueno", "ganado", "perdido"]


def test_resolver_clave_existente(db):
    F.seed_fases(db)
    assert F.resolver_clave(db, "oficina_bueno") == "oficina_bueno"


def test_borrar_base_mueve_leads_y_fallback(db):
    F.seed_fases(db)
    db.add(EmaLead(phone="1", estado="oficina_bueno"))
    db.commit()
    fase = db.query(Fase).filter(Fase.clave == "oficina_bueno").first()
    # borrar Oficina Bueno
    ent = db.query(Fase).filter(Fase.rol == "entrada", Fase.id != fase.id).first()
    db.query(EmaLead).filter(EmaLead.estado == fase.clave).update({EmaLead.estado: ent.clave})
    db.delete(fase)
    db.commit()
    # el lead se movió y un futuro oficina_bueno cae a residencial_bueno
    assert db.query(EmaLead).filter(EmaLead.phone == "1").first().estado == "nuevo"
    assert F.resolver_clave(db, "oficina_bueno") == "residencial_bueno"


def test_resolver_a_entrada_si_no_hay_equivalente(db):
    F.seed_fases(db)
    # dejar solo 'nuevo'
    for f in db.query(Fase).filter(Fase.clave != "nuevo").all():
        db.delete(f)
    db.commit()
    assert F.resolver_clave(db, "oficina_bueno") == "nuevo"
