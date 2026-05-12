# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Pytest fixtures backed by a real Postgres container.

These tests bring up a Postgres 16 image via ``testcontainers``,
apply Alembic migrations, and exercise the schema in the same way
production will. They are the only place that catches Postgres-only
issues: JSONB indexing, ``TIMESTAMP WITH TIME ZONE`` round-trip,
CHECK-constraint enforcement, and FK CASCADE without the SQLite
PRAGMA workaround.

Skipped automatically when the Docker daemon is not reachable —
no point failing the whole suite on a dev box without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest

# Skip the whole package when testcontainers / docker are unavailable.
docker = pytest.importorskip("docker")
testcontainers_postgres = pytest.importorskip("testcontainers.postgres")

try:
    docker.from_env().ping()
except Exception as exc:
    pytest.skip(
        f"Docker daemon not reachable, skipping Postgres integration tests: {exc}",
        allow_module_level=True,
    )

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from callendulla.db.session import (  # noqa: E402
    create_engine,
    create_session_factory,
)

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """One Postgres container shared across all integration tests.

    Container startup takes ~5s. Tests share it because they use
    isolated schemas via DROP/CREATE in per-test teardown, not via
    per-test container.
    """
    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg


@pytest.fixture(scope="session")
def sync_dsn(postgres_container: PostgresContainer) -> str:
    """psycopg2 sync DSN for Alembic."""
    return postgres_container.get_connection_url(driver="psycopg2")


@pytest.fixture(scope="session")
def async_dsn(postgres_container: PostgresContainer) -> str:
    """asyncpg DSN for SQLAlchemy async engine."""
    return postgres_container.get_connection_url(driver="asyncpg")


@pytest.fixture(scope="session")
def alembic_cfg(sync_dsn: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sync_dsn)
    return cfg


@pytest.fixture
async def fresh_db(
    alembic_cfg: Config,
    async_dsn: str,
) -> AsyncIterator[AsyncSession]:
    """Apply migrations, yield an AsyncSession, teardown the schema.

    Each test gets a freshly migrated database. Teardown drops the
    public schema CASCADE so the next test starts clean — cheaper
    than ``alembic downgrade base`` because Alembic can't always
    reverse complex graphs.
    """
    command.upgrade(alembic_cfg, "head")
    engine = create_engine(async_dsn)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
        # Drop everything so the next test starts clean.
        teardown_engine = create_engine(async_dsn)
        async with teardown_engine.begin() as conn:
            from sqlalchemy import text  # noqa: PLC0415

            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await teardown_engine.dispose()
