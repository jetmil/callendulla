# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Alembic environment.

Pulls the DSN from :class:`callendulla.config.Settings` so a single
``.env`` is the source of truth for both the application and migrations.
``target_metadata`` is the same :data:`Base.metadata` the runtime models
populate, so autogenerate compares against the live schema.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing ``callendulla.db`` registers every model under Base.metadata
# — autogenerate needs that to see the full schema.
from callendulla.db import Base

config = context.config

# Apply logging config from alembic.ini only when running standalone.
# When invoked from tests we want pytest's own logging untouched.
if config.config_file_name is not None and not os.environ.get("CALLENDULLA_TEST_RUN"):
    fileConfig(config.config_file_name)


_PLACEHOLDER_DSN = "postgresql+psycopg2://placeholder/placeholder"


def _resolve_db_url() -> str:
    """Pick the migration DSN.

    Precedence:
        1. ``CALLENDULLA_MIGRATION_DSN`` env (used by integration tests)
        2. ``ALEMBIC_DB_URL`` env (used by ad-hoc runs)
        3. Programmatic override via ``config.set_main_option("sqlalchemy.url", ...)``
           — used by pytest's ``alembic.command.upgrade(cfg, ...)`` smoke tests
        4. ``Settings.db_dsn_sync`` (default — driven by .env)
    """
    explicit = os.environ.get("CALLENDULLA_MIGRATION_DSN") or os.environ.get("ALEMBIC_DB_URL")
    if explicit:
        return explicit

    programmatic = config.get_main_option("sqlalchemy.url")
    if programmatic and programmatic != _PLACEHOLDER_DSN:
        return programmatic

    # Lazy import to keep ``alembic --help`` working without a full Settings env.
    from callendulla.config import get_settings  # noqa: PLC0415

    return get_settings().db_dsn_sync


target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emit SQL without a live connection."""
    url = _resolve_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — open a real DB connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_db_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
