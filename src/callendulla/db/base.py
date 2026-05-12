# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Declarative base + cross-table mixins.

Every model inherits :class:`Base`. Most also pick up :class:`TimestampMixin`
to get ``created_at`` / ``updated_at`` columns wired to the DB's clock —
keeping the source of truth on the DB makes replication and concurrent
writes correct.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Consistent naming convention so Alembic-generated migrations have
# deterministic constraint names — without this, autogenerate produces
# names like ``ix_user_a1b2c3d4`` that change between runs.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Project-wide declarative base.

    Subclasses MUST live under :mod:`callendulla.db.models` so the
    ``callendulla.db`` package init imports them and populates
    ``Base.metadata.tables`` before Alembic looks at it.
    """

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


class TimestampMixin:
    """``created_at`` / ``updated_at`` columns backed by ``NOW()``."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
