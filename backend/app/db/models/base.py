"""Shared SQLAlchemy declarative base and mixins.

Every model in this package:
  - uses a UUID primary key (`id`), generated server-side by Postgres
    (`gen_random_uuid()` from the pgcrypto/pgcrypto-free `uuid-ossp`-less
    builtin in PG13+), so IDs are never guessable/sequential.
  - gets `created_at` / `updated_at` timestamps automatically.

A fixed naming convention is applied to constraints/indexes so that
Alembic's autogenerate produces stable, diffable migration names instead
of the default SQLAlchemy-assigned ones.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,  # applied client-side for normal ORM inserts
        server_default=text("gen_random_uuid()"),  # fallback for raw SQL/seed scripts
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
