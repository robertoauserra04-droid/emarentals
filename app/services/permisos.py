"""Permisos granulares del panel (patrón Regional México: rol + secciones).

Modelo: cada usuario tiene un `rol` y una lista `secciones` (claves del catálogo). El rol
`dueno` es comodín (ve TODO e ignora la lista). El resto ve solo las secciones asignadas.
El `asesor` además conserva el scoping de datos (solo sus leads/visitas) que ya vive en los
routers de leads/visitas — esto es visibilidad de SECCIÓN, complementaria a ese scoping.
"""

# (clave, etiqueta) — orden = orden del menú. Secciones REALES de EMA Rentals (panel mínimo).
SECCIONES: list[tuple[str, str]] = [
    ("leads",    "Leads / Pipeline"),
    ("bandeja",  "Bandeja de conversaciones"),
    ("resumen",  "Resumen (perfiles)"),
    ("metricas", "Métricas"),
    ("contexto", "Contexto del bot"),
    ("usuarios", "Usuarios"),
]

CLAVES_VALIDAS = {c for c, _ in SECCIONES}

# Presets por rol (lo que se asigna por default al crear; el dueño edita a gusto).
DEFAULT_SECCIONES = {
    "dueno":       list(CLAVES_VALIDAS),
    "coordinador": ["leads", "bandeja", "resumen", "metricas"],
    "asesor":      ["leads", "bandeja"],
}


def secciones_de(user) -> list[str]:
    """Secciones efectivas de un usuario (dueño = todas)."""
    if getattr(user, "rol", None) == "dueno":
        return [c for c, _ in SECCIONES]
    return list(getattr(user, "secciones", None) or [])


def puede(user, seccion: str) -> bool:
    """¿El usuario puede entrar a `seccion`?"""
    if user is None:
        return False
    if getattr(user, "rol", None) == "dueno":
        return True
    return seccion in (getattr(user, "secciones", None) or [])


def limpiar_secciones(valores) -> list[str]:
    """Sanea el input del form contra el catálogo (descarta claves inválidas, sin duplicados)."""
    if not valores:
        return []
    vistos: list[str] = []
    for v in valores:
        v = (v or "").strip()
        if v in CLAVES_VALIDAS and v not in vistos:
            vistos.append(v)
    return vistos
