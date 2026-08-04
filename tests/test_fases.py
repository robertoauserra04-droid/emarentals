"""Fases configurables: seed, borrar cualquier fase, y ruteo con fallback (resolver_clave)."""
from app.models.lead import EmaLead, Fase
from app.routers import fases as F


def test_seed_crea_las_base(db):
    F.seed_fases(db)
    claves = [f.clave for f in F.listar_fases(db)]
    assert claves == ["nuevo", "descartado", "pregunton",
                      "residencial_baja", "oficina_baja",
                      "interesado_oficina", "interesado_residencial",
                      "residencial_normal", "oficina_mid",
                      "residencial_bueno", "oficina_bueno"]


def test_ganado_y_perdido_no_son_fases(db):
    """Cerrar o perder es un desenlace (`es_venta` / `motivo_perdida`), no una columna."""
    F.seed_fases(db)
    claves = {f.clave for f in F.listar_fases(db)}
    assert "ganado" not in claves and "perdido" not in claves


def test_adopta_columnas_creadas_a_mano(db):
    """El caso de producción: el admin creó las columnas desde el panel, así que nacieron con
    rol 'custom' y clave del slug — y el bot no podía rutear hacia ellas."""
    db.add(Fase(clave="nuevo", nombre="Nuevo", orden=0, rol="entrada", activa=True))
    db.add(Fase(clave="oficina_mid_2", nombre="Oficina Mid", orden=1, rol="custom", activa=True))
    db.add(Fase(clave="pregunt_n", nombre="Preguntón", orden=2, rol="custom", activa=True))
    db.commit()

    F.seed_fases(db)

    adoptada = db.query(Fase).filter(Fase.nombre == "Oficina Mid").first()
    assert adoptada.clave == "oficina_mid" and adoptada.rol == "mid_oficina"
    # El acento no impide el emparejamiento.
    preg = db.query(Fase).filter(Fase.nombre == "Preguntón").first()
    assert preg.clave == "pregunton" and preg.rol == "pregunton"
    # Y ahora el bot sí llega ahí.
    assert F.resolver_clave(db, "oficina_mid") == "oficina_mid"


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
    # el lead se movió y un futuro oficina_bueno cae en el equivalente más cercano
    assert db.query(EmaLead).filter(EmaLead.phone == "1").first().estado == "nuevo"
    assert F.resolver_clave(db, "oficina_bueno") == "oficina_mid"


def test_resolver_a_entrada_si_no_hay_equivalente(db):
    F.seed_fases(db)
    # dejar solo 'nuevo'
    for f in db.query(Fase).filter(Fase.clave != "nuevo").all():
        db.delete(f)
    db.commit()
    assert F.resolver_clave(db, "oficina_bueno") == "nuevo"
