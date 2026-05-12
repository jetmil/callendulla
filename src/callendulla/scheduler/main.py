# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Scheduler entry point: APScheduler ticking once a minute.

Started by supervisord as a dedicated process — the api and bot
processes never run the scheduler, so we don't race on triggers when
the deployment scales horizontally.

The only knob worth pointing out is ``misfire_grace_time=60``. The
APScheduler default is **1 second**, which silently drops any job
whose scheduled time gets blocked by a brief asyncio hiccup. That hid
a multi-day outage in the private predecessor; never again.
"""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from callendulla.bot import create_bot
from callendulla.config import Settings, get_settings
from callendulla.core.safelog import install_loguru_redactor
from callendulla.db.session import SessionFactory, get_session_factory
from callendulla.llm import build_provider
from callendulla.llm.base import LLMProvider
from callendulla.scheduler.nudge_engine import NudgeEngine

if TYPE_CHECKING:
    from aiogram import Bot

_TICK_SECONDS: int = 60
_MISFIRE_GRACE_SECONDS: int = 60


def build_scheduler(engine: NudgeEngine) -> AsyncIOScheduler:
    """Construct an :class:`AsyncIOScheduler` wired to ``engine.run_once``."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        engine.run_once,
        trigger="interval",
        seconds=_TICK_SECONDS,
        # See module docstring — APScheduler default of 1s is a footgun.
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
        coalesce=True,  # if we lag, run once not N times
        max_instances=1,  # never two ticks in flight at once
        id="nudge_tick",
        name="nudge_tick",
    )
    return scheduler


async def _async_run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    install_loguru_redactor()

    bot: Bot = create_bot(settings)
    factory: SessionFactory = get_session_factory()

    # BYOK: the provider is wired up here. If the operator's key is
    # bad / model unreachable, the engine's per-call try/except falls
    # back to the template bank — boot does not fail.
    llm: LLMProvider | None
    try:
        llm = build_provider(settings)
        logger.info("LLM provider: {} ({})", settings.llm_provider.value, settings.llm_model)
    except ValueError as exc:
        logger.warning("LLM disabled: {} — falling back to template bank", exc)
        llm = None

    # aiogram.Bot.send_message accepts chat_id positionally + many optional
    # kwargs, so it does not match our kwarg-only Protocol on paper —
    # at runtime it satisfies the call site fine.
    engine = NudgeEngine(factory, bot, llm)  # type: ignore[arg-type]

    scheduler = build_scheduler(engine)
    scheduler.start()
    logger.info("scheduler: started, tick every {}s", _TICK_SECONDS)

    # Wait until SIGTERM / SIGINT. supervisord stops us by signal.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):  # Windows fallback
            loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        logger.info("scheduler: shutting down")
        scheduler.shutdown(wait=True)
        await bot.session.close()


def run() -> None:
    """Synchronous entry point — what supervisord exec's."""
    asyncio.run(_async_run())


if __name__ == "__main__":
    run()
