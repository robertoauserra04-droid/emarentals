"""Fases del pipeline (columnas del Kanban) — el centro de mando.

El admin renombra, recolora, reordena y agrega/quita columnas. Y sobre todo: **cada fase decide
qué pasa cuando un lead cae en ella** — si se notifica, a quién, con qué mensaje de cierre, y si
entra al motor de recuperación. Antes todo eso estaba hardcodeado dentro del bot.

Las 6 fases base tienen `rol` fijo (el bot las usa por su `clave`); las que agrega el admin son
`custom` (manuales). Ver `flow-clasificacion.md`.
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.lead import ContactoAlerta, EmaLead, Fase
from app.models.user import User
from app.services import auth
from app.services.bot import leads as leads_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fases", tags=["fases"])

# Fases base — la taxonomía que diseñó EMA. Dos ejes: residencial vs oficina, y tres niveles de
# prioridad. Más tres fuera de la matriz: Nuevo (entrada), Preguntón y Descartado.
#
#                        │ Residencial              │ Oficina
#   ─────────────────────┼──────────────────────────┼───────────────────────────
#   clasificación a medias│ Interesado Residencial   │ Interesado Oficina
#   prioridad baja        │ Residencial Baja Prior.  │ Oficina Baja Prioridad
#   prioridad media       │ Residencial Normal       │ Oficina Mid
#   prioridad alta        │ Residencial Bueno        │ Oficina Bueno
#
# El nivel sale de UMBRAL + PLAZO: quien cumple el umbral de EMA (casa siempre, depto ≥2
# recámaras, oficina ≥100 m² o ≥20 personas) cae en Bueno si renta 12+ meses y en Normal/Mid si
# renta menos. Quien no cumple el umbral cae en Baja Prioridad, sin importar el plazo. Así una
# casa NUNCA cae en baja prioridad, que es la regla que dio el cliente.
_CIERRE_ASESOR = ("Perfecto, gracias por la información. En unos momentos un asesor se pondrá en "
                  "contacto con usted.")
_CIERRE_DESCARTE = ("Gracias por su interés. En esta ocasión no podremos ofrecerle el servicio, ya "
                    "que su solicitud no cumple con nuestros criterios de renta.")

FASES_DEFAULT = [
    {
        "clave": "nuevo", "nombre": "Nuevo", "color": "#6b8ba4", "rol": "entrada",
        "descripcion": "Lead recién llegado que aún no ha iniciado el proceso de clasificación.",
        "criterios": "Entra aquí automáticamente en cuanto escribe por primera vez.",
        "notificar": False, "recuperar": False, "mensaje_cierre": None,
    },
    {
        "clave": "descartado", "nombre": "Descartado", "color": "#a9603f", "rol": "descartado",
        "descripcion": "Solicitud que no cumple con los criterios mínimos de renta.",
        "criterios": "El prospecto declinó o el bot registró un motivo de pérdida.",
        "notificar": False, "recuperar": False, "mensaje_cierre": None,
    },
    {
        "clave": "pregunton", "nombre": "Preguntón", "color": "#9a9188", "rol": "pregunton",
        "descripcion": "Persona interesada únicamente en obtener información.",
        "criterios": "Preguntó por el servicio pero dijo que no busca rentar, o se negó a contestar el cuestionario.",
        "notificar": False, "recuperar": True, "mensaje_cierre": None,
    },
    {
        "clave": "residencial_baja", "nombre": "Residencial Baja Prioridad", "color": "#c8a98a",
        "rol": "baja_residencial",
        "descripcion": "Prospecto residencial con bajo potencial comercial.",
        "criterios": "Departamento de 1 recámara. No llega al umbral, sin importar el plazo.",
        "notificar": False, "recuperar": True, "mensaje_cierre": _CIERRE_DESCARTE,
    },
    {
        "clave": "oficina_baja", "nombre": "Oficina Baja Prioridad", "color": "#c89a6a",
        "rol": "baja_oficina",
        "descripcion": "Prospecto de oficina con bajo potencial comercial.",
        "criterios": "Oficina menor a 100 m² y de menos de 20 personas. No llega al umbral, sin importar el plazo.",
        "notificar": False, "recuperar": True, "mensaje_cierre": _CIERRE_DESCARTE,
    },
    {
        "clave": "interesado_oficina", "nombre": "Interesado Oficina", "color": "#d9b45c",
        "rol": "interesado_oficina",
        "descripcion": "Prospecto interesado en una oficina cuya clasificación aún está incompleta.",
        "criterios": "Dijo que es para oficina, pero falta el tamaño (m² o personas) o el tiempo de renta.",
        "notificar": False, "recuperar": True, "mensaje_cierre": None,
    },
    {
        "clave": "interesado_residencial", "nombre": "Interesado Residencial", "color": "#d9c05c",
        "rol": "interesado_residencial",
        "descripcion": "Prospecto residencial cuya clasificación aún está incompleta.",
        "criterios": "Dijo que es para casa o departamento, pero faltan las recámaras o el tiempo de renta.",
        "notificar": False, "recuperar": True, "mensaje_cierre": None,
    },
    {
        "clave": "residencial_normal", "nombre": "Residencial Normal", "color": "#8fb08a",
        "rol": "normal_residencial",
        "descripcion": "Prospecto residencial con prioridad media.",
        "criterios": "Casa, o departamento de 2 o más recámaras, pero con renta de menos de 12 meses.",
        "notificar": False, "recuperar": False, "mensaje_cierre": _CIERRE_ASESOR,
    },
    {
        "clave": "oficina_mid", "nombre": "Oficina Mid", "color": "#7ba883",
        "rol": "mid_oficina",
        "descripcion": "Prospecto con potencial comercial medio.",
        "criterios": "Oficina de 100 m² o más, o de 20 personas o más, pero con renta de menos de 12 meses.",
        "notificar": False, "recuperar": False, "mensaje_cierre": _CIERRE_ASESOR,
    },
    {
        "clave": "residencial_bueno", "nombre": "Residencial Bueno", "color": "#5b8f6a",
        "rol": "bueno_residencial",
        "descripcion": "Prospecto residencial prioritario.",
        "criterios": "Casa, o departamento de 2 o más recámaras, con renta de 12 meses o más.",
        "notificar": True, "recuperar": False, "mensaje_cierre": _CIERRE_ASESOR,
    },
    {
        "clave": "oficina_bueno", "nombre": "Oficina Bueno", "color": "#3f7d55",
        "rol": "bueno_oficina",
        "descripcion": "Prospecto prioritario para atención comercial.",
        "criterios": "Oficina de 100 m² o más, o de 20 personas o más, con renta de 12 meses o más.",
        "notificar": True, "recuperar": False, "mensaje_cierre": _CIERRE_ASESOR,
    },
]
# Ganado y Perdido NO son fases: cerrar una venta o perder un prospecto no es "estar en una
# columna", es un desenlace. Se marcan con las banderas que ya tiene el lead (`es_venta` y
# `motivo_perdida`) y el lead se queda en la fase donde estaba.
_POR_CLAVE = {f["clave"]: f for f in FASES_DEFAULT}

# Instalaciones que ya tienen estas columnas creadas a mano desde el panel: sus claves salieron
# del slug del nombre y su rol quedó en "custom", así que el bot NO puede rutear hacia ellas
# (todo cae a la fase de entrada). Este mapa las adopta emparejando por NOMBRE.
_ADOPTAR_POR_NOMBRE = {
    "nuevo": "nuevo",
    "descartado": "descartado",
    "pregunton": "pregunton",
    "residencial baja prioridad": "residencial_baja",
    "oficina baja prioridad": "oficina_baja",
    "interesado oficina": "interesado_oficina",
    "interesado residencial": "interesado_residencial",
    "residencial normal": "residencial_normal",
    "oficina mid": "oficina_mid",
    "residencial bueno": "residencial_bueno",
    "oficina bueno": "oficina_bueno",
    # Nombres de la versión anterior del pipeline.
    "interesado": "interesado_residencial",
    "low priority": "residencial_baja",
}


def _normaliza(nombre: str) -> str:
    """Minúsculas sin acentos ni signos, para emparejar nombres escritos a mano."""
    import unicodedata
    s = unicodedata.normalize("NFD", (nombre or "").strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _adoptar_columnas_manuales(db: Session) -> bool:
    """Reconecta las columnas que el admin creó a mano con el ruteo del bot.

    Una fase creada desde el panel nace con `rol="custom"` y una clave derivada del nombre, así
    que `resolver_clave` no la encuentra y los leads acaban todos en la fase de entrada. Aquí se
    emparejan por nombre con la taxonomía base y se les asigna su clave y su rol.
    """
    cambios = False
    usadas = {f.clave for f in db.query(Fase).all()}
    for f in db.query(Fase).all():
        destino = _ADOPTAR_POR_NOMBRE.get(_normaliza(f.nombre))
        base = _POR_CLAVE.get(destino) if destino else None
        if not base:
            continue
        # La clave la reasignamos solo si hace falta y nadie más la ocupa.
        if f.clave != destino and destino not in usadas:
            usadas.discard(f.clave)
            usadas.add(destino)
            f.clave = destino
            cambios = True
            logger.warning("[fases] columna '%s' adoptada como '%s'", f.nombre, destino)
        # El ROL siempre: sin él, `resolver_clave` no puede usarla como fallback cuando el admin
        # borra otra columna, y los leads acabarían en la fase de entrada.
        if f.clave == destino and f.rol != base["rol"]:
            f.rol = base["rol"]
            cambios = True
    return cambios


def seed_fases(db: Session) -> None:
    if db.query(Fase).count() == 0:
        for i, f in enumerate(FASES_DEFAULT):
            db.add(Fase(orden=i, activa=True, **f))
        db.commit()
        return

    cambios = _adoptar_columnas_manuales(db)

    # Backfill de despliegues previos. `_ensure_columns` agrega las columnas nuevas pero las deja
    # en NULL en las filas existentes, así que aquí se les pone su valor de arranque.
    for f in db.query(Fase).all():
        base = _POR_CLAVE.get(f.clave)
        if f.notificar is None:
            f.notificar = bool(base["notificar"]) if base else False
            cambios = True
        if f.recuperar is None:
            f.recuperar = bool(base["recuperar"]) if base else False
            cambios = True
        if base is None:
            continue
        if not f.descripcion and not f.criterios:
            f.descripcion, f.criterios = base["descripcion"], base["criterios"]
            cambios = True
        # "Tipo 1 / Tipo 2" no le decía nada a nadie en EMA: se reemplaza aunque ya estuviera
        # guardado (el backfill de arriba solo toca los vacíos).
        elif f.criterios and "Tipo 1 =" in f.criterios:
            f.criterios = base["criterios"]
            cambios = True
        if f.mensaje_cierre is None and base["mensaje_cierre"]:
            f.mensaje_cierre = base["mensaje_cierre"]
            cambios = True
    if cambios:
        db.commit()


def seed_contactos(db: Session) -> None:
    """Migra los teléfonos de ALERTA_ADMIN_TELEFONOS al directorio, la primera vez.

    Sin esto se perdería la configuración que EMA ya tiene en el .env al pasar al directorio.
    """
    if db.query(ContactoAlerta).count() > 0:
        return
    telefonos = [t.strip() for t in (settings.alerta_admin_telefonos or "").split(",") if t.strip()]
    if not telefonos:
        return
    for i, tel in enumerate(telefonos):
        db.add(ContactoAlerta(nombre=f"Administrador {i + 1}" if i else "Administrador",
                              telefono=tel))
    db.commit()


def _dto(f: Fase) -> dict:
    return {"id": f.id, "clave": f.clave, "nombre": f.nombre, "color": f.color,
            "orden": f.orden, "rol": f.rol, "activa": bool(f.activa),
            "descripcion": f.descripcion or "", "criterios": f.criterios or "",
            "notificar": bool(f.notificar), "recuperar": bool(f.recuperar),
            "mensaje_cierre": f.mensaje_cierre or "",
            "editable_borrar": True}


# Rol semántico de cada clave base (para rutear con fallback si borran la fase destino).
_ROL_DE_CLAVE = {f["clave"]: f["rol"] for f in FASES_DEFAULT}
# Si el admin borró la columna destino, el lead cae en la más parecida. El orden importa: primero
# el mismo nivel del otro giro, luego el nivel de al lado, y al final la entrada.
_FALLBACK_ROL = {
    "bueno_residencial":      ["normal_residencial", "bueno_oficina", "entrada"],
    "bueno_oficina":          ["mid_oficina", "bueno_residencial", "entrada"],
    "normal_residencial":     ["bueno_residencial", "mid_oficina", "entrada"],
    "mid_oficina":            ["bueno_oficina", "normal_residencial", "entrada"],
    "baja_residencial":       ["baja_oficina", "descartado", "entrada"],
    "baja_oficina":           ["baja_residencial", "descartado", "entrada"],
    "interesado_residencial": ["interesado_oficina", "entrada"],
    "interesado_oficina":     ["interesado_residencial", "entrada"],
    "pregunton":              ["entrada"],
    "descartado":             ["baja_residencial", "entrada"],
    "entrada": [],
    # Roles de la versión anterior del pipeline, por si quedan filas viejas.
    "pipeline": ["interesado_residencial", "entrada"],
    "lowpri":   ["baja_residencial", "entrada"],
}


def resolver_clave(db: Session, clave: str | None) -> str:
    """Devuelve una clave de fase EXISTENTE. Si la deseada fue borrada, cae a una equivalente por
    rol (buen prospecto → otra 'bueno' o a la entrada). Así borrar cualquier fase no rompe el bot."""
    fs = listar_fases(db)
    claves = {f.clave for f in fs}
    if clave in claves:
        return clave
    porrol = {f.rol: f.clave for f in fs}
    rol = _ROL_DE_CLAVE.get(clave or "", "")
    for r in ([rol] + _FALLBACK_ROL.get(rol, [])):
        if r in porrol:
            return porrol[r]
    return fs[0].clave if fs else (clave or "nuevo")


def listar_fases(db: Session) -> list[Fase]:
    seed_fases(db)
    return db.query(Fase).filter(Fase.activa == True).order_by(Fase.orden.asc()).all()  # noqa: E712


def fase_por_clave(db: Session, clave: str | None) -> Fase | None:
    if not clave:
        return None
    return db.query(Fase).filter(Fase.clave == clave).first()


def claves_con_recuperacion(db: Session) -> list[str]:
    """Fases marcadas para recuperación. Lo consume `recovery._elegibles`."""
    return [f.clave for f in db.query(Fase).filter(Fase.recuperar.is_(True),
                                                   Fase.activa == True).all()]  # noqa: E712


def telefonos_alerta(db: Session) -> list[str]:
    """A quién se le avisa. Lista GLOBAL: la misma para todas las fases que notifican.
    Vacía = el caller cae a ALERTA_ADMIN_TELEFONOS del .env."""
    filas = (db.query(ContactoAlerta)
             .filter(ContactoAlerta.activo.is_(True))
             .order_by(ContactoAlerta.id.asc()).all())
    return [c.telefono for c in filas]


@router.get("")
def listar(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    return [_dto(f) for f in listar_fases(db)]


@router.get("/score-info")
def score_info(user: User = Depends(auth.current_user)):
    """Cómo se reparten los 100 puntos del score. Se lee de las MISMAS constantes que hace el
    cálculo, así el popover de ayuda del panel no puede desincronizarse del cálculo real."""
    return {
        "tamano": {
            "total": 45,
            "oficina": [{"m2": m2, "personas": p, "pts": pts}
                        for m2, p, pts in leads_svc.TABLA_TAMANO_OFICINA]
                       + [{"m2": None, "personas": None, "pts": leads_svc.TAMANO_OFICINA_MIN}],
            "recamaras": [{"recamaras": r, "pts": pts}
                          for r, pts in leads_svc.TABLA_TAMANO_RECAMARAS],
        },
        "plazo": {"total": 35, "tabla": leads_svc.TABLA_PLAZO},
        "tipo": {"total": 20, "tabla": leads_svc.TABLA_TIPO},
        "nota": ("El score solo ordena leads dentro de una columna: no dispara notificaciones ni "
                 "mueve fases. La fase la decide la regla de EMA, así que score y fase pueden "
                 "discrepar."),
    }


# ─────────── Contactos a los que se puede avisar ───────────

class ContactoIn(BaseModel):
    nombre: str
    telefono: str


@router.get("/contactos")
def listar_contactos(db: Session = Depends(get_db), user: User = Depends(auth.current_user)):
    filas = db.query(ContactoAlerta).order_by(ContactoAlerta.id.asc()).all()
    return [{"id": c.id, "nombre": c.nombre, "telefono": c.telefono, "activo": bool(c.activo)}
            for c in filas]


@router.post("/contactos")
def crear_contacto(data: ContactoIn, db: Session = Depends(get_db),
                   user: User = Depends(auth.solo_dueno)):
    nombre = data.nombre.strip()
    telefono = leads_svc.norm_phone(data.telefono.strip())
    if not nombre or not telefono:
        raise HTTPException(422, "Nombre y teléfono son obligatorios")
    existente = db.query(ContactoAlerta).filter(ContactoAlerta.telefono == telefono).first()
    if existente:
        existente.nombre, existente.activo = nombre, True
        db.commit()
        return {"id": existente.id, "nombre": existente.nombre, "telefono": existente.telefono,
                "activo": True}
    c = ContactoAlerta(nombre=nombre, telefono=telefono)
    db.add(c)
    db.commit()
    return {"id": c.id, "nombre": c.nombre, "telefono": c.telefono, "activo": True}


@router.delete("/contactos/{contacto_id}")
def borrar_contacto(contacto_id: int, db: Session = Depends(get_db),
                    user: User = Depends(auth.solo_dueno)):
    c = db.query(ContactoAlerta).filter(ContactoAlerta.id == contacto_id).first()
    if not c:
        raise HTTPException(404, "Contacto no encontrado")
    db.delete(c)
    db.commit()
    return {"ok": True}


# ─────────── CRUD de fases ───────────

class FaseIn(BaseModel):
    nombre: str
    color: str | None = None
    descripcion: str | None = None
    criterios: str | None = None
    notificar: bool | None = None
    recuperar: bool | None = None
    mensaje_cierre: str | None = None


@router.post("")
def crear(data: FaseIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Agrega una columna nueva (rol custom, manual). Arranca sin notificar ni recuperar: una fase
    nueva no debe empezar mandando mensajes sin que nadie lo haya pedido."""
    nombre = data.nombre.strip()
    if not nombre:
        raise HTTPException(422, "El nombre es obligatorio")
    base = re.sub(r"[^a-z0-9]+", "_", nombre.lower()).strip("_") or "fase"
    clave = base
    n = 1
    while db.query(Fase).filter(Fase.clave == clave).first():
        n += 1
        clave = f"{base}_{n}"
    orden = (db.query(Fase).count())
    f = Fase(clave=clave, nombre=nombre, color=data.color or "#8c725d", orden=orden,
             rol="custom", activa=True, descripcion=(data.descripcion or "").strip() or None,
             criterios=(data.criterios or "").strip() or None,
             notificar=bool(data.notificar), recuperar=bool(data.recuperar),
             mensaje_cierre=(data.mensaje_cierre or "").strip() or None)
    db.add(f)
    db.flush()
    db.commit()
    return _dto(f)


@router.put("/{fase_id}")
def editar(fase_id: int, data: FaseIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Renombra / recolora / reconfigura una fase (la clave y el rol NO cambian)."""
    f = db.query(Fase).filter(Fase.id == fase_id).first()
    if not f:
        raise HTTPException(404, "Fase no encontrada")
    if data.nombre.strip():
        f.nombre = data.nombre.strip()
    if data.color:
        f.color = data.color
    if data.descripcion is not None:
        f.descripcion = data.descripcion.strip()
    if data.criterios is not None:
        f.criterios = data.criterios.strip()
    if data.notificar is not None:
        f.notificar = bool(data.notificar)
    if data.recuperar is not None:
        f.recuperar = bool(data.recuperar)
    if data.mensaje_cierre is not None:
        f.mensaje_cierre = data.mensaje_cierre.strip() or None
    db.commit()
    return _dto(f)


class OrdenIn(BaseModel):
    ids: list[int]   # ids en el orden deseado


@router.post("/reordenar")
def reordenar(data: OrdenIn, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    for i, fid in enumerate(data.ids):
        f = db.query(Fase).filter(Fase.id == fid).first()
        if f:
            f.orden = i
    db.commit()
    return {"ok": True}


@router.delete("/{fase_id}")
def borrar(fase_id: int, db: Session = Depends(get_db), user: User = Depends(auth.solo_dueno)):
    """Borra CUALQUIER fase. Sus leads se mueven a otra fase (la de entrada, o la primera que quede).
    No se puede borrar la última (debe quedar al menos una columna)."""
    f = db.query(Fase).filter(Fase.id == fase_id).first()
    if not f:
        raise HTTPException(404, "Fase no encontrada")
    if db.query(Fase).count() <= 1:
        raise HTTPException(400, "Debe quedar al menos una fase en el pipeline")
    # Destino de los leads: la fase de entrada si no es la que se borra; si no, la primera que quede.
    destino = None
    entrada = db.query(Fase).filter(Fase.rol == "entrada", Fase.id != f.id).first()
    if entrada:
        destino = entrada.clave
    else:
        otra = db.query(Fase).filter(Fase.id != f.id).order_by(Fase.orden.asc()).first()
        destino = otra.clave if otra else None
    if destino:
        db.query(EmaLead).filter(EmaLead.estado == f.clave).update({EmaLead.estado: destino})
    db.delete(f)
    db.commit()
    return {"ok": True, "movidos_a": destino}
