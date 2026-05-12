# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Database layer: SQLAlchemy 2 declarative models and async session factory.

The :class:`callendulla.db.base.Base` declarative is the metadata target
Alembic autogenerates against. Importing this package side-loads every
model module so ``Base.metadata.tables`` is complete.
"""

from callendulla.db import models  # noqa: F401 — side-effect: register tables
from callendulla.db.base import Base
from callendulla.db.session import (
    SessionFactory,
    create_engine,
    create_session_factory,
    get_engine,
    get_session,
    get_session_factory,
)

__all__ = [
    "Base",
    "SessionFactory",
    "create_engine",
    "create_session_factory",
    "get_engine",
    "get_session",
    "get_session_factory",
]
