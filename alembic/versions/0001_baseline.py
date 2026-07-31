"""baseline: esquema completo desde los modelos (dialecto-correcto, idempotente).

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-21

Crea todo el esquema desde Base.metadata con checkfirst (no re-crea tablas existentes). Así el
cutover en una BD que YA existe es seguro: `alembic upgrade head` corre esto como no-op y solo
registra la versión (baseline = estado actual). En una BD vacía, crea todo.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401  (registra todas las tablas)
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401
    Base.metadata.drop_all(bind=op.get_bind())
