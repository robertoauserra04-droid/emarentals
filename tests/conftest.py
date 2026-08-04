"""Fixtures: BD SQLite en memoria por test."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.models  # noqa: F401  (registra todas las tablas)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def usuario():
    """Dueño autenticado, para llamar los endpoints como funciones sin levantar HTTP."""
    from app.models.user import User
    return User(id=1, email="admin@ema-rentals.local", nombre="Admin", rol="dueno", activo=True)
