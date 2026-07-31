"""Métricas de leads con filtro por fechas — cómo va el filtrado en un rango.

GET /api/metrics?desde=YYYY-MM-DD&hasta=YYYY-MM-DD
Devuelve totales, embudo por etapa, por canal, por segmento, tasas y la serie diaria.
El rango filtra por `created_at` del lead (cuándo entró). Sin rango = todo.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lead import EmaLead
from app.models.user import User
from app.services import auth

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

_BUENOS = ("calificado", "asignado", "ganado")
_ESTADOS = ["nuevo", "interesado", "calificado", "asignado", "ganado", "perdido"]
_CANALES = ["whatsapp", "instagram", "messenger"]
_SEGMENTOS = ["residencial", "oficina", "airbnb", "corporativo"]


def _parse(d: str | None) -> datetime | None:
    if not d:
        return None
    try:
        return datetime.strptime(d[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _fecha(dt) -> str | None:
    if dt is None:
        return None
    return dt.date().isoformat()


@router.get("")
def metricas(desde: str | None = None, hasta: str | None = None,
             db: Session = Depends(get_db), user: User = Depends(auth.requiere_seccion("metricas"))):
    d0 = _parse(desde)
    d1 = _parse(hasta)
    q = db.query(EmaLead)
    if d0:
        q = q.filter(EmaLead.created_at >= d0)
    if d1:
        q = q.filter(EmaLead.created_at < d1 + timedelta(days=1))  # 'hasta' inclusivo
    leads = q.all()

    total = len(leads)
    por_estado = Counter(l.estado or "nuevo" for l in leads)
    por_canal = Counter((l.source or "whatsapp") for l in leads)
    por_segmento = Counter((l.segmento or "sin definir") for l in leads)
    por_marca = Counter((l.marca or "sin definir") for l in leads)
    por_modelo = Counter((l.modelo or "sin definir") for l in leads)
    por_tipo = Counter((l.tipo_propiedad or "sin definir") for l in leads)
    buenos = sum(1 for l in leads if (l.estado or "") in _BUENOS)
    ganados = por_estado.get("ganado", 0)
    perdidos = por_estado.get("perdido", 0)
    alertados = sum(1 for l in leads if l.alertado_at is not None)
    scores = [l.score_calif or 0 for l in leads]
    score_prom = round(sum(scores) / len(scores)) if scores else 0

    # Serie diaria (entrada de leads + cuántos buenos por día)
    dias: dict[str, dict] = {}
    for l in leads:
        f = _fecha(l.created_at)
        if not f:
            continue
        d = dias.setdefault(f, {"fecha": f, "total": 0, "buenos": 0})
        d["total"] += 1
        if (l.estado or "") in _BUENOS:
            d["buenos"] += 1
    serie = [dias[k] for k in sorted(dias.keys())]

    def _pct(n):
        return round(100 * n / total) if total else 0

    return {
        "total": total,
        "buenos": buenos,
        "ganados": ganados,
        "perdidos": perdidos,
        "alertados": alertados,
        "score_promedio": score_prom,
        "tasa_calificacion": _pct(buenos),   # % de leads que llegaron a buen lead
        "tasa_ganado": _pct(ganados),
        "por_estado": {e: por_estado.get(e, 0) for e in _ESTADOS},
        "por_canal": {c: por_canal.get(c, 0) for c in _CANALES},
        "por_marca": {k: v for k, v in por_marca.items() if v},
        "por_modelo": {k: v for k, v in por_modelo.items() if v},
        "por_tipo": {k: v for k, v in por_tipo.items() if v},
        "por_segmento": {s: por_segmento.get(s, 0) for s in _SEGMENTOS
                         if por_segmento.get(s, 0)} | (
                         {"sin definir": por_segmento.get("sin definir", 0)}
                         if por_segmento.get("sin definir") else {}),
        "serie": serie,
        "rango": {"desde": desde, "hasta": hasta},
    }
