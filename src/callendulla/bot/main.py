# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Bot wiring: :class:`Bot` + :class:`Dispatcher` factories and CLI entry.

The polling entry point :func:`run_polling` is what
``deploy/supervisord.conf`` invokes. Webhook mode is wired separately in
a later PR — it shares the same dispatcher but mounts onto the FastAPI
app instead of running ``start_polling``.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from callendulla.bot.handlers import (
    events as events_router,
    help as help_router,
    reactions as reactions_router,
    source as source_router,
    start as start_router,
)
from callendulla.bot.middleware.user import UserMiddleware
from callendulla.config import Settings, get_settings
from callendulla.db.session import SessionFactory, get_session_factory


def create_bot(settings: Settings | None = None) -> Bot:
    """Build a configured :class:`Bot` instance."""
    settings = settings or get_settings()
    return Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
) -> Dispatcher:
    """Build a fully wired :class:`Dispatcher` with middleware and routers.

    Pass ``session_factory`` in tests to inject an in-memory engine —
    otherwise the lru_cached production factory is used.
    """
    settings = settings or get_settings()
    factory = session_factory or get_session_factory()

    dp = Dispatcher()

    # Stash references so handlers can pull them out of the workflow
    # data dict without touching module-level singletons. This makes
    # them swappable in tests.
    dp["settings"] = settings
    dp["session_factory"] = factory

    # ``UserMiddleware`` runs first on every update — it materialises a
    # :class:`User` row (creating one if absent and allowed) and adds it
    # to the workflow data under the key ``user``.
    dp.update.middleware(UserMiddleware(factory, settings))

    dp.include_router(start_router.router)
    dp.include_router(events_router.router)
    dp.include_router(reactions_router.router)
    dp.include_router(source_router.router)
    dp.include_router(help_router.router)

    return dp


async def run_polling(settings: Settings | None = None) -> None:
    """Start the bot in long-polling mode. Used by supervisord."""
    settings = settings or get_settings()
    bot = create_bot(settings)
    dispatcher = create_dispatcher(settings)
    # ``allowed_updates=dispatcher.resolve_used_update_types()`` tells
    # Telegram only to send us the kinds we actually handle, reducing
    # noise and rate-limit pressure.
    await dispatcher.start_polling(
        bot,
        allowed_updates=dispatcher.resolve_used_update_types(),
    )
