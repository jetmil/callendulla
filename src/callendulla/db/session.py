# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Async SQLAlchemy engine and session factory.

Two layers of caching here:

- :func:`get_engine` / :func:`get_session_factory` are module-level
  singletons backed by ``functools.lru_cache``. Process-wide.
- :func:`get_session` is the FastAPI/aiogram dependency-injection helper:
  yields one session per call, commits on exit, rolls back on exception.

Tests bypass the singletons by constructing :func:`create_engine` and
:func:`create_session_factory` directly against their own DSN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from callendulla.config import get_settings

# PEP 695 type alias — Python 3.12+ syntax.
type SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async SQLAlchemy engine without touching the cache.

    Useful in tests where each case needs its own connection pool against
    an ephemeral container.

    SQLite's aiosqlite driver uses a single in-process connection, so we
    skip the QueuePool tuning — passing ``pool_size`` to it raises
    ``TypeError``. Production target is Postgres + asyncpg; SQLite is
    only here for unit-test metadata smoke.
    """
    kwargs: dict[str, object] = {"echo": echo}
    if not dsn.startswith("sqlite"):
        # ``pre_ping`` round-trips a SELECT 1 before each checkout — costs
        # ~1ms but recovers transparently from idle-pruned connections,
        # which Postgres + supervisord restarts will produce often.
        # Match docker-compose's expected concurrency: api + bot +
        # scheduler each open a few connections. 20 total is generous;
        # bump if you see ``QueuePool limit ... reached`` in logs.
        kwargs.update(
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
        )
    return create_async_engine(dsn, **kwargs)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Process-wide async engine, built from current :class:`Settings`."""
    settings = get_settings()
    return create_engine(settings.db_dsn)


@lru_cache(maxsize=1)
def get_session_factory() -> SessionFactory:
    """Process-wide session factory."""
    return create_session_factory(get_engine())


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency-injection helper.

    Use with FastAPI::

        @router.get("/...")
        async def handler(session: AsyncSession = Depends(get_session)):
            ...

    Yields one session per call. Commits on success, rolls back on
    exception. The caller never sees the commit step.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
