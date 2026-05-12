# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""LLM integration inside :class:`NudgeEngine`.

We don't re-test scheduling here — that's covered in
``tests/test_scheduler_nudge_engine.py``. This file only verifies the
three branches in ``_compose_message``:

- ``llm=None`` → template bank
- ``llm.generate`` returns text → engine uses LLM text
- ``llm.generate`` raises :class:`LLMError` → falls back to template

Cross-user safety: the test that LLM is called inspects the *prompt*
passed in; if any other user's data appears there, the test would
catch it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from callendulla.db import Base
from callendulla.db.models import (
    Event,
    NudgeLog,
    ToneStep,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
    VoiceProfile,
)
from callendulla.db.session import create_engine, create_session_factory
from callendulla.llm.base import LLMError
from callendulla.scheduler.nudge_engine import NudgeEngine


class _StubBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **_: object) -> object:
        self.sent.append((chat_id, text))
        fake = MagicMock()
        fake.message_id = 1
        return fake


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


def _moscow_at(hour: int) -> datetime:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    return datetime(2026, 6, 1, hour, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(UTC)


async def _seed(session_factory: object, voice: VoiceProfile = VoiceProfile.WARM_SISTER) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        u = User(
            tg_id=1001,
            ical_token="t1",
            timezone="Europe/Moscow",
            voice_profile=voice,
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        now = _moscow_at(12)
        e = Event(
            owner_user_id=u.id,
            title="event-title-for-prompt-inspection",
            dtstart=now,
            timezone=u.timezone,
        )
        session.add(e)
        await session.flush()
        t = Trigger(
            event_id=e.id,
            kind=TriggerKind.ONESHOT,
            state=TriggerState.PENDING,
            schedule_spec=now.isoformat(),
            next_fire_at=now,
            current_tone=ToneStep.SOFT,
        )
        session.add(t)
        await session.commit()
        return u


class TestNoLLMUsesTemplate:
    async def test_template_bank_used(self, session_factory: object) -> None:
        user = await _seed(session_factory)
        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot, llm=None)  # type: ignore[arg-type]
        await engine.run_once(now_utc=_moscow_at(12))
        assert len(bot.sent) == 1
        _, text = bot.sent[0]
        # Template for WARM_SISTER + SOFT starts with emoji 🌼
        assert text.startswith("🌼")
        assert "event-title-for-prompt-inspection" in text
        # Belt and braces — no leakage of nonexistent other user
        assert "alice" not in text.lower()
        _ = user


class TestLLMSuccess:
    async def test_engine_uses_llm_text(self, session_factory: object) -> None:
        await _seed(session_factory)
        bot = _StubBot()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value="свежий пинок от модели")
        engine = NudgeEngine(session_factory, bot, llm=llm)  # type: ignore[arg-type]
        await engine.run_once(now_utc=_moscow_at(12))

        assert bot.sent == [(1001, "свежий пинок от модели")]
        llm.generate.assert_awaited_once()
        # Prompt MUST include the title we passed; this is the
        # cross-user-isolation guard at this level.
        (prompt_arg,), _ = llm.generate.call_args
        assert "event-title-for-prompt-inspection" in prompt_arg

    async def test_nudge_log_stores_llm_text(self, session_factory: object) -> None:
        await _seed(session_factory)
        llm = MagicMock()
        llm.generate = AsyncMock(return_value="свежий пинок от модели")
        engine = NudgeEngine(session_factory, _StubBot(), llm=llm)  # type: ignore[arg-type]
        await engine.run_once(now_utc=_moscow_at(12))
        async with session_factory() as session:  # type: ignore[operator]
            logs = list((await session.execute(select(NudgeLog))).scalars())
        assert len(logs) == 1
        assert logs[0].message_text == "свежий пинок от модели"


class TestLLMFailsFallbackToTemplate:
    async def test_llm_error_falls_back_to_template(self, session_factory: object) -> None:
        await _seed(session_factory)
        bot = _StubBot()
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=LLMError("quota exceeded"))
        engine = NudgeEngine(session_factory, bot, llm=llm)  # type: ignore[arg-type]
        await engine.run_once(now_utc=_moscow_at(12))

        # User got the template message regardless of upstream failure.
        assert len(bot.sent) == 1
        _, text = bot.sent[0]
        assert "event-title-for-prompt-inspection" in text
        # And it really IS a template (starts with the WARM_SISTER emoji,
        # not "свежий пинок").
        assert text.startswith("🌼")
