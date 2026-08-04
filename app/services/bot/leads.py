"""CRUD de EmaLead + la CALIFICACIÓN determinista (el bot filtra, el código decide).

Flujo (renta): tipo de propiedad → pregunta ligada → tiempo de renta. Reglas de BUEN PROSPECTO:
  - Casa: siempre buen prospecto (por el simple hecho de ser casa).
  - Departamento: buen prospecto si tiene 2 o más recámaras.
  - Oficina: buen prospecto si es de 100 m² o más, o 20 personas o más.
Categoría de oficina (por plazo): 0-12 meses = renta corta; 12+ meses = renta larga.

NADA se clasifica hasta que `cuestionario_completo()` sea True: el bot tiene que TERMINAR de
preguntar antes de que el código califique, notifique o lo apague. Ver `flow-clasificacion.md`.

El score (0-100) es SOLO para priorizar dentro de una columna del Kanban; no dispara nada.
El bot nunca cierra venta ni demo; al terminar de filtrar, escala a un asesor.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.lead import EmaLead


def norm_phone(phone: str) -> str:
    """52XXXXXXXXXX — quita el + y el 1 de móvil que México agrega en WA (521...).
    Para IG/Messenger el identificador es un PSID; se deja tal cual."""
    d = (phone or "").lstrip("+")
    if len(d) == 13 and d.startswith("521"):
        d = "52" + d[3:]
    return d


def _nombre_generico(nombre: str | None, phone: str) -> bool:
    if not nombre:
        return True
    n = nombre.strip()
    return n == "" or n.lower() == "cliente" or n == str(phone)


def get_or_create_lead(db: Session, phone: str, name: str | None = None,
                       channel: str = "whatsapp") -> EmaLead:
    name_real = name.strip() if (name and not _nombre_generico(name, phone)) else None
    lead = db.query(EmaLead).filter(EmaLead.phone == phone).first()
    if not lead:
        lead = EmaLead(phone=phone, name=name_real, estado="nuevo", source=channel)
        db.add(lead)
        db.flush()
    elif name_real and _nombre_generico(lead.name, phone):
        lead.name = name_real
    return lead


def touch(lead: EmaLead) -> None:
    lead.message_count = (lead.message_count or 0) + 1
    lead.last_message_at = datetime.now(timezone.utc)


def _marca_de(tp: str | None) -> str | None:
    if tp == "oficina":
        return "office"
    if tp in ("casa", "departamento"):
        return "rentals"
    return None


def evaluar_prospecto(lead: EmaLead) -> tuple[bool, str | None]:
    """Devuelve (es_buen_prospecto, tipo_oficina). Reglas del cliente."""
    tp = lead.tipo_propiedad
    if tp == "casa":
        bueno = True
    elif tp == "departamento":
        bueno = (lead.recamaras or 0) >= 2
    elif tp == "oficina":
        bueno = (lead.oficina_m2 or 0) >= 100 or (lead.oficina_personas or 0) >= 20
    else:
        bueno = False
    tipo_of = None
    if tp == "oficina":
        tipo_of = "tipo2" if lead.tiempo_renta == "12+" else "tipo1"
    return bueno, tipo_of


# Etiquetas visibles de la categoría de oficina. Las claves internas (tipo1/tipo2) NO cambian
# para no romper datos existentes; lo que cambia es cómo se lee, porque "Tipo 1 / Tipo 2" no le
# dice nada a nadie en EMA.
TIPO_OFICINA_LBL = {
    "tipo1": "Oficina · renta corta (hasta 1 año)",
    "tipo2": "Oficina · renta larga (12 meses o más)",
}


# Claves ESTABLES de las fases (el admin renombra el nombre visible, no la clave).
FASE_ENTRADA = "nuevo"
FASE_PREGUNTON = "pregunton"
FASE_DESCARTADO = "descartado"
FASE_INTERESADO_RES = "interesado_residencial"
FASE_INTERESADO_OFI = "interesado_oficina"
FASE_BAJA_RES = "residencial_baja"
FASE_BAJA_OFI = "oficina_baja"
FASE_NORMAL_RES = "residencial_normal"
FASE_MID_OFI = "oficina_mid"
FASE_BUENO_RES = "residencial_bueno"
FASE_BUENO_OFI = "oficina_bueno"

# Ganado y Perdido NO son fases: son desenlaces, y viven en las banderas `es_venta` y
# `motivo_perdida` del lead. Un lead cerrado se queda en la columna donde estaba.


def _cerrado(lead: EmaLead) -> bool:
    """¿El asesor ya le dio un desenlace? El bot no vuelve a moverlo de fase."""
    return bool(lead.es_venta) or bool(lead.motivo_perdida)


# Columnas de las que el bot todavía puede sacar a un lead. Si el asesor ya lo movió a otra,
# se respeta. Se incluyen las claves del pipeline anterior porque los leads que ya están en la
# base siguen guardando `estado="interesado"` aunque la fase se haya renombrado.
_ANTESALA = (None, FASE_ENTRADA, FASE_PREGUNTON, FASE_INTERESADO_RES, FASE_INTERESADO_OFI,
             "interesado", "low_priority")


def _es_oficina(lead: EmaLead) -> bool:
    return lead.tipo_propiedad == "oficina"


def fase_calificada(lead: EmaLead, bueno: bool) -> str:
    """A qué columna cae un lead con el cuestionario terminado.

    Dos ejes: giro (residencial / oficina) y nivel (baja / media / alta). El nivel sale de
    UMBRAL + PLAZO — quien cumple el umbral de EMA cae en Bueno si renta 12+ meses, y en
    Normal/Mid si renta menos. Quien no lo cumple cae en Baja Prioridad sin importar el plazo,
    así una casa nunca aterriza ahí (regla del cliente).
    """
    oficina = _es_oficina(lead)
    if not bueno:
        return FASE_BAJA_OFI if oficina else FASE_BAJA_RES
    if lead.tiempo_renta == "12+":
        return FASE_BUENO_OFI if oficina else FASE_BUENO_RES
    return FASE_MID_OFI if oficina else FASE_NORMAL_RES


def fase_incompleta(lead: EmaLead) -> str:
    """Dónde espera un lead al que todavía le faltan respuestas."""
    if not lead.tipo_propiedad:
        return FASE_ENTRADA
    return FASE_INTERESADO_OFI if _es_oficina(lead) else FASE_INTERESADO_RES


def falta_del_cuestionario(lead: EmaLead) -> list[str]:
    """Qué preguntas del flujo siguen sin respuesta. Vacío = cuestionario terminado.

    Es la fuente ÚNICA de "¿ya terminó?": el prompt pregunta tipo → dato ligado → tiempo, y aquí
    se exige exactamente eso. Antes la casa se daba por completa sin recámaras (porque "casa
    siempre es buen prospecto"), y por eso el bot cerraba saltándose una pregunta.
    """
    tp = lead.tipo_propiedad
    falta: list[str] = []
    if not tp:
        return ["tipo de propiedad", "tiempo de renta"]
    if tp in ("casa", "departamento"):
        if lead.recamaras is None:
            falta.append("recámaras")
    elif tp == "oficina":
        if lead.oficina_m2 is None and lead.oficina_personas is None:
            falta.append("metros cuadrados o número de personas")
    else:
        falta.append("tipo de propiedad")
    if not lead.tiempo_renta:
        falta.append("tiempo de renta")
    return falta


def cuestionario_completo(lead: EmaLead) -> bool:
    """¿El bot ya terminó de preguntar? Hasta que esto sea True no se clasifica, no se notifica
    y no se apaga el bot."""
    return not falta_del_cuestionario(lead)


# ─────────── Score de PRIORIDAD (0-100) ───────────
# Solo sirve para ordenar leads dentro de una columna del Kanban: no dispara alertas, no mueve
# fases y nada automático depende de él. La fase la decide la regla de EMA, no este número — por
# eso score y fase PUEDEN discrepar (una casa de 1 recámara cae en Residencial Bueno con score
# bajo: le toca a un asesor, pero de último).
#
# Se reparte entre las 3 señales que el cuestionario realmente recoge. Para afinarlo edita estas
# tablas, no la lógica; `GET /api/fases/score-info` las expone tal cual al panel, así que el
# popover de ayuda nunca puede desincronizarse del cálculo real.

# Oficina: (m² mínimos, personas mínimas, puntos). De mayor a menor; basta cumplir UNO de los dos.
TABLA_TAMANO_OFICINA = [(300, 40, 45), (150, 25, 36), (100, 20, 28), (50, 10, 15)]
TAMANO_OFICINA_MIN = 6          # oficina por debajo del último escalón

# Casa / departamento: (recámaras mínimas, puntos).
TABLA_TAMANO_RECAMARAS = [(4, 45), (3, 36), (2, 27), (1, 12)]
TAMANO_RECAMARAS_MIN = 6

TABLA_PLAZO = {"12+": 35, "6-12": 20, "0-6": 7}
TABLA_TIPO = {"oficina": 20, "casa": 16, "departamento": 11}


def _pts_tamano(lead: EmaLead) -> int:
    if lead.tipo_propiedad == "oficina":
        m2, personas = (lead.oficina_m2 or 0), (lead.oficina_personas or 0)
        for m2_min, personas_min, pts in TABLA_TAMANO_OFICINA:
            if m2 >= m2_min or personas >= personas_min:
                return pts
        return TAMANO_OFICINA_MIN
    recamaras = lead.recamaras or 0
    for recamaras_min, pts in TABLA_TAMANO_RECAMARAS:
        if recamaras >= recamaras_min:
            return pts
    return TAMANO_RECAMARAS_MIN


def desglose_score(lead: EmaLead) -> dict:
    """De dónde salió cada punto. El panel lo muestra junto al número para que no sea opaco."""
    if not cuestionario_completo(lead):
        return {"tamano": 0, "plazo": 0, "tipo": 0, "total": 0, "completo": False}
    tamano = _pts_tamano(lead)
    plazo = TABLA_PLAZO.get(lead.tiempo_renta or "", 0)
    tipo = TABLA_TIPO.get(lead.tipo_propiedad or "", 0)
    return {"tamano": tamano, "plazo": plazo, "tipo": tipo,
            "total": min(100, tamano + plazo + tipo), "completo": True}


def recompute_score_calif(lead: EmaLead) -> int:
    """Score 0-100 de prioridad. Es 0 mientras el cuestionario esté incompleto."""
    lead.score_calif = desglose_score(lead)["total"]
    return lead.score_calif


def _texto_prospecto(lead: EmaLead, bueno: bool, tipo_of: str | None) -> str:
    """Lo que ve el MODELO como resultado de la tool. Si falta algo se lo decimos: así sabe que
    todavía tiene que preguntar en vez de darse por cerrado."""
    falta = falta_del_cuestionario(lead)
    if falta:
        return "dato registrado. Todavía falta preguntar: " + ", ".join(falta)
    if lead.tipo_propiedad == "oficina" and tipo_of:
        return TIPO_OFICINA_LBL.get(tipo_of, "oficina") + (" · buen prospecto" if bueno else "")
    return "buen prospecto" if bueno else "prospecto en evaluación"


def apply_capturar_lead(lead: EmaLead, args: dict) -> str:
    """Aplica la tool capturar_lead y CLASIFICA de forma determinista (el bot filtra)."""
    if args.get("nombre") and not lead.name:
        lead.name = args["nombre"].strip()

    # Enteros
    for campo in ("recamaras", "oficina_m2", "oficina_personas"):
        v = args.get(campo)
        if v not in (None, ""):
            try:
                setattr(lead, campo, int(v))
            except (TypeError, ValueError):
                pass
    # Strings (COALESCE: vacío no sobreescribe)
    for campo in ("tipo_propiedad", "tiempo_renta", "uso", "zona", "presupuesto",
                  "nivel_interes", "que_pregunto", "resumen", "motivo_perdida"):
        v = args.get(campo)
        if v not in (None, ""):
            setattr(lead, campo, v)

    # Derivados
    if lead.tipo_propiedad:
        lead.marca = _marca_de(lead.tipo_propiedad)
        if not lead.modelo:
            lead.modelo = "renta"
    # Clasificar SOLO con el cuestionario terminado. Antes `es_buen_prospecto` se ponía en True
    # con solo saber el tipo de propiedad, y el handler escalaba ahí mismo: ese era el bug que
    # apagaba el bot y notificaba antes de hacer la última pregunta.
    completo = cuestionario_completo(lead)
    bueno, tipo_of = evaluar_prospecto(lead) if completo else (False, None)
    lead.es_buen_prospecto = bueno
    lead.tipo_oficina = tipo_of

    # Fase. Orden de prioridad: descartado → preguntón → clasificado → esperando respuestas.
    # El bot no toca un lead al que el asesor ya le dio un desenlace (venta o pérdida).
    if args.get("motivo_perdida"):
        lead.estado = FASE_DESCARTADO
    elif not _cerrado(lead):
        if args.get("solo_informacion") and not completo:
            # Curioso: preguntó por el servicio pero no busca rentar. Si ya contestó todo el
            # cuestionario mandan los datos, no la etiqueta.
            lead.estado = FASE_PREGUNTON
        elif completo:
            lead.estado = fase_calificada(lead, bueno)
        elif lead.estado in _ANTESALA:
            # Solo se mueve dentro de la antesala: si el asesor ya lo puso en otra columna
            # a mano, se respeta.
            lead.estado = fase_incompleta(lead)

    recompute_score_calif(lead)
    return "prospecto actualizado: " + _texto_prospecto(lead, bueno, tipo_of)


def mark_won(db: Session, lead: EmaLead, monto: str | None = None) -> None:
    """Cierra la venta. El lead se queda en su columna: ganado es un desenlace, no una fase."""
    lead.es_venta = True
    if monto:
        lead.monto_cierre = monto
    db.commit()


def mark_lost(db: Session, lead: EmaLead, motivo: str | None = None) -> None:
    lead.motivo_perdida = motivo or "sin motivo"
    db.commit()
