# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""FastAPI application factory and module-level singleton.

Lifespan:
- on startup: install loguru token redactor, eagerly read Settings so a
  misconfig crashes the worker before it accepts traffic.
- on shutdown: nothing — async resources clean themselves up.

Middleware stack (outermost → innermost):
1. ``TrustedHostMiddleware`` — Host header allowlist, blocks Host
   injection. Configured from ``Settings.allowed_hosts``.
2. ``CORSMiddleware`` — closed by default, opened only for
   ``Settings.cors_origins``.
3. ``AGPLSourceHeaderMiddleware`` — injects ``X-Source-URL`` into
   every response. Must run innermost so its header survives any
   future response-rewriting middleware (e.g. compression).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from callendulla._version import __version__
from callendulla.api.middleware.agpl_source import AGPLSourceHeaderMiddleware
from callendulla.api.routes import health, source
from callendulla.config import get_settings
from callendulla.core.safelog import install_loguru_redactor

if TYPE_CHECKING:
    from callendulla.config import Settings


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    install_loguru_redactor()
    # Trigger eager Settings construction so a misconfig surfaces here,
    # not on the first request.
    get_settings()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a new FastAPI application.

    Pass an explicit ``settings`` to bypass the lru_cached singleton —
    useful for tests that need a different env snapshot.
    """
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Callendulla",
        version=__version__,
        description=(
            "Self-hosted Telegram calendar bot with escalating reminder tone "
            "and voice diary. AGPL-3.0."
        ),
        lifespan=_lifespan,
    )

    # 1. Host allowlist (outermost). When ALLOWED_HOSTS=["*"] this is a
    # no-op pass-through — fine for dev, dangerous for prod (operator's
    # responsibility per SECURITY.md).
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.allowed_hosts,
    )

    # 2. CORS. Empty origins list means no preflight is approved —
    # browser apps from other origins can't talk to the API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # 3. AGPL §13 X-Source-URL on every response.
    app.add_middleware(
        AGPLSourceHeaderMiddleware,
        source_url=str(settings.agpl_source_url),
    )

    # Routers — order is irrelevant for routing, only for OpenAPI grouping.
    app.include_router(health.router)
    app.include_router(source.router)

    return app


_lazy_app: FastAPI | None = None


def __getattr__(name: str) -> object:
    """PEP 562 hook: build the module-level ``app`` on first access.

    Eager construction at import time would call :func:`get_settings`,
    which fails without a full ``.env`` — breaking unit tests that only
    want to import the factory. ``uvicorn callendulla.api:app`` still
    works: it accesses ``app``, this hook builds it, the same instance
    is returned on every subsequent access.
    """
    if name == "app":
        global _lazy_app  # noqa: PLW0603 — singleton bootstrap
        if _lazy_app is None:
            _lazy_app = create_app()
        return _lazy_app
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
