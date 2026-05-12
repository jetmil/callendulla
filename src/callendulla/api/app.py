# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""FastAPI application factory and module-level singleton.

Lifespan:
- on startup: install loguru token redactor, eagerly read Settings so a
  misconfig crashes the worker before it accepts traffic. In webhook
  mode also register the Telegram webhook URL.
- on shutdown: in webhook mode, drop the Telegram webhook and close
  the aiogram Bot session. In polling mode, nothing to do.

Middleware stack (outermost → innermost):
1. ``TrustedHostMiddleware`` — Host header allowlist, blocks Host
   injection. Configured from ``Settings.allowed_hosts``.
2. ``CORSMiddleware`` — closed by default, opened only for
   ``Settings.cors_origins``.
3. ``RateLimitMiddleware`` — per-IP sliding window on ``/ical/`` paths.
   Operators can disable it via ``ICAL_RATE_LIMIT_PER_IP_HOURLY=0``.
4. ``AGPLSourceHeaderMiddleware`` — injects ``X-Source-URL`` into
   every response. Must run innermost so its header survives any
   future response-rewriting middleware (e.g. compression).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from callendulla._version import __version__
from callendulla.api.middleware.agpl_source import AGPLSourceHeaderMiddleware
from callendulla.api.middleware.rate_limit import RateLimitMiddleware
from callendulla.api.routes import health, ical, source
from callendulla.api.webhook import build_webhook_router
from callendulla.bot import create_bot, create_dispatcher
from callendulla.config import BotMode, get_settings
from callendulla.core.rate_limit import RateLimiter
from callendulla.core.safelog import install_loguru_redactor

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher

    from callendulla.config import Settings


def _make_lifespan(
    settings: Settings,
    bot: Bot | None,
    dispatcher: Dispatcher | None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a lifespan tied to a specific Bot/Dispatcher pair.

    ``bot`` / ``dispatcher`` are ``None`` in polling mode; the lifespan
    still installs the loguru redactor but does no webhook bookkeeping.
    """

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        install_loguru_redactor()
        # Trigger eager Settings construction so misconfig surfaces here,
        # not on the first request. ``settings`` is already constructed
        # via the closure, but the cached singleton must be primed too —
        # other code paths read it through ``get_settings()``.
        get_settings()

        webhook_active = (
            settings.bot_mode is BotMode.WEBHOOK and bot is not None and dispatcher is not None
        )
        if webhook_active:
            assert bot is not None  # narrow for type checker
            assert dispatcher is not None
            assert settings.webhook_secret is not None  # validator guarantees
            url = settings.webhook_url()
            assert url is not None
            logger.info("setting Telegram webhook → {}", url)
            await bot.set_webhook(
                url=url,
                secret_token=settings.webhook_secret.get_secret_value(),
                allowed_updates=dispatcher.resolve_used_update_types(),
                drop_pending_updates=True,
            )

        try:
            yield
        finally:
            if webhook_active:
                assert bot is not None
                logger.info("deleting Telegram webhook on shutdown")
                try:
                    await bot.delete_webhook(drop_pending_updates=False)
                except Exception:
                    logger.exception("failed to delete webhook during shutdown")
                await bot.session.close()

    return _lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a new FastAPI application.

    Pass an explicit ``settings`` to bypass the lru_cached singleton —
    useful for tests that need a different env snapshot.
    """
    if settings is None:
        settings = get_settings()

    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    if settings.bot_mode is BotMode.WEBHOOK:
        bot = create_bot(settings)
        dispatcher = create_dispatcher(settings)

    app = FastAPI(
        title="Callendulla",
        version=__version__,
        description=(
            "Self-hosted Telegram calendar bot with escalating reminder tone "
            "and voice diary. AGPL-3.0."
        ),
        lifespan=_make_lifespan(settings, bot, dispatcher),
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

    # 3. Per-IP rate-limit on /ical/. Activated only if the operator
    # configured a positive limit; we still build the Redis client so
    # tests can swap it. The async client is created lazily — module
    # import is fine without a reachable Redis.
    if settings.ical_rate_limit_per_ip_hourly > 0:
        from redis.asyncio import Redis as _AsyncRedis  # noqa: PLC0415

        redis_client = _AsyncRedis.from_url(settings.redis_url, decode_responses=False)
        app.add_middleware(
            RateLimitMiddleware,
            limiter=RateLimiter(redis_client),
            ical_per_ip_hourly=settings.ical_rate_limit_per_ip_hourly,
        )

    # 4. AGPL §13 X-Source-URL on every response.
    app.add_middleware(
        AGPLSourceHeaderMiddleware,
        source_url=str(settings.agpl_source_url),
    )

    # Routers — order is irrelevant for routing, only for OpenAPI grouping.
    app.include_router(health.router)
    app.include_router(source.router)
    app.include_router(ical.router)

    if (
        settings.bot_mode is BotMode.WEBHOOK
        and bot is not None
        and dispatcher is not None
        and settings.webhook_secret is not None
        and settings.webhook_path is not None
    ):
        app.include_router(
            build_webhook_router(
                bot=bot,
                dispatcher=dispatcher,
                secret_token=settings.webhook_secret.get_secret_value(),
                path=settings.webhook_path,
            )
        )

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
