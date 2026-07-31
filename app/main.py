"""App FastAPI de EMA Rentals — filtro de leads multicanal.

Monta solo lo de Emma: webhooks (WhatsApp Kapso + Instagram/Messenger Sinch), bandeja de
conversaciones, kanban de leads, auth y usuarios. Sin finanzas, reportes, propiedades ni agenda.
Puerta de activación: en prod aborta si `validar_config()` encuentra problemas.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings, validar_config
from app.database import Base, SessionLocal, engine
from app.routers import (auth as auth_router, conversaciones, leads,
                         recovery as recovery_router, sinch_webhook, usuarios, webhook)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# --- Puerta de activación (fail-fast en prod) ---
_problemas = validar_config()
if _problemas and settings.env == "prod":
    raise RuntimeError("Config insegura, no arranco:\n- " + "\n- ".join(_problemas))
for p in _problemas:
    logger.warning("[config] %s", p)

app = FastAPI(title="EMA Rentals — Filtro de leads (Vella)")

# CORS acotado (no '*' con credenciales). En dev, el panel corre en el mismo origen.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.panel_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook.router)          # WhatsApp (Kapso)
app.include_router(sinch_webhook.router)    # Instagram + Messenger (Sinch)
app.include_router(conversaciones.router)   # bandeja unificada 3 canales
app.include_router(leads.router)            # kanban de leads
app.include_router(auth_router.router)
app.include_router(usuarios.router)
app.include_router(recovery_router.router)  # recuperación (apagada por default)


@app.get("/health")
def health():
    return {"ok": True, "env": settings.env}


def _run_alembic_upgrade() -> None:
    """Corre `alembic upgrade head` programáticamente (prod/Postgres)."""
    import os
    from alembic import command
    from alembic.config import Config
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(base, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(base, "alembic"))
    command.upgrade(cfg, "head")
    logger.warning("[migra] alembic upgrade head OK")


def _bootstrap() -> None:
    """Prepara el esquema, el admin inicial y los settings de recovery."""
    if engine.dialect.name == "postgresql":
        _run_alembic_upgrade()
    else:
        Base.metadata.create_all(bind=engine)   # dev/tests SQLite
    _ensure_columns()   # red de seguridad idempotente
    from app.models.user import User
    from app.services import auth, recovery
    db = SessionLocal()
    try:
        recovery.seed_defaults(db)
        if settings.admin_password and not db.query(User).filter(User.email == settings.admin_email).first():
            db.add(User(email=settings.admin_email, nombre=settings.admin_name, rol="dueno",
                        password_hash=auth.hash_password(settings.admin_password)))
            db.commit()
            logger.warning("[bootstrap] admin creado: %s", settings.admin_email)
        _backfill_defaults(db)
    finally:
        db.close()


def _backfill_defaults(db) -> None:
    """Rellena defaults en filas viejas (users.activo en NULL rompería el login)."""
    from sqlalchemy import text
    try:
        db.execute(text("UPDATE users SET activo = true WHERE activo IS NULL"))
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        logger.warning("[migra] backfill users.activo falló: %s", e)


def _ensure_columns() -> None:
    """Migración inline GENÉRICA e idempotente: agrega columnas del modelo que falten en la BD."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    for tabla in Base.metadata.sorted_tables:
        if not insp.has_table(tabla.name):
            continue
        existentes = {c["name"] for c in insp.get_columns(tabla.name)}
        for col in tabla.columns:
            if col.name in existentes:
                continue
            try:
                tipo = col.type.compile(dialect=engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {tabla.name} ADD COLUMN {col.name} {tipo}'))
                logger.warning("[migra] +%s.%s (%s)", tabla.name, col.name, tipo)
            except Exception as e:  # noqa: BLE001
                logger.warning("[migra] no se pudo añadir %s.%s: %s", tabla.name, col.name, e)


@app.on_event("startup")
def _startup():
    _bootstrap()
    # Nota: la recuperación automática está apagada (recovery_enabled=false). Si EMA la enciende,
    # reactivar aquí el scheduler:  threading.Thread(target=_recovery_loop, daemon=True).start()


# Panel estático (si existe la carpeta frontend/).
try:
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
except Exception:  # noqa: BLE001
    logger.warning("[main] carpeta frontend/ no montada")
