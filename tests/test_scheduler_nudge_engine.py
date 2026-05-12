# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Integration tests for :class:`NudgeEngine`.

Runs the full firing loop against an in-memory SQLite DB with a stub
"bot" that captures :py:meth:`send_message` calls. Verifies:

- Due triggers fire and produce :class:`NudgeLog` rows
- Tone escalates by one step per fire
- Quiet hours defer instead of fire
- Cap-escalation after N silent iterations snoozes for 12 h and resets
- Other-user events are not touched (cross-user isolation)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callendulla.db import Base
from callendulla.db.models import (
    Event,
    NudgeLog,
    NudgeReaction,
    ToneStep,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
    VoiceProfile,
)
from callendulla.db.session import create_engine, create_session_factory
from callendulla.scheduler.nudge_engine import NudgeEngine
from callendulla.scheduler.tones import (
    CAP_ITERATIONS_WITHOUT_REACTION,
    CAP_SNOOZE,
    CAP_TONE,
)


class _StubBot:
    """Captures send_message calls; tests inspect ``self.sent``."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self._counter = 0

    async def send_message(self, *, chat_id: int, text: str, **_: object) -> object:
        self.sent.append((chat_id, text))
        self._counter += 1

        class _FakeMessage:
            message_id = self._counter

        return _FakeMessage()


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _make_user(
    session: AsyncSession,
    *,
    tg_id: int,
    ical_token: str,
    quiet_from: int = 22,
    quiet_to: int = 9,
    voice_profile: VoiceProfile = VoiceProfile.WARM_SISTER,
) -> User:
    user = User(
        tg_id=tg_id,
        ical_token=ical_token,
        timezone="Europe/Moscow",
        quiet_from_hour=quiet_from,
        quiet_to_hour=quiet_to,
        voice_profile=voice_profile,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_event_with_trigger(
    session: AsyncSession,
    *,
    owner: User,
    title: str,
    dtstart: datetime,
    next_fire_at: datetime,
    current_tone: ToneStep = ToneStep.SOFT,
    iteration_count: int = 0,
) -> Trigger:
    event = Event(
        owner_user_id=owner.id,
        title=title,
        dtstart=dtstart,
        timezone=owner.timezone,
    )
    session.add(event)
    await session.flush()
    trigger = Trigger(
        event_id=event.id,
        kind=TriggerKind.ONESHOT,
        state=TriggerState.PENDING,
        schedule_spec=dtstart.isoformat(),
        next_fire_at=next_fire_at,
        current_tone=current_tone,
        iteration_count=iteration_count,
    )
    session.add(trigger)
    await session.commit()
    await session.refresh(trigger)
    return trigger


# ── Helper: 10:00 Europe/Moscow on 1 June 2026 in UTC ────────────────
def _moscow_at(hour: int, minute: int = 0) -> datetime:
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    local = datetime(2026, 6, 1, hour, minute, tzinfo=ZoneInfo("Europe/Moscow"))
    return local.astimezone(UTC)


class TestFires:
    async def test_due_trigger_produces_nudge_log_and_sends(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)  # mid-day, not quiet
            await _make_event_with_trigger(
                session,
                owner=u,
                title="standup",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        touched = await engine.run_once(now_utc=now_utc)

        assert touched == 1
        assert len(bot.sent) == 1
        chat_id, text = bot.sent[0]
        assert chat_id == u.tg_id
        assert "standup" in text

        async with session_factory() as session:  # type: ignore[operator]
            logs = list((await session.execute(select(NudgeLog))).scalars())
        assert len(logs) == 1
        assert logs[0].message_text == text
        assert logs[0].tone_used is ToneStep.SOFT

    async def test_not_due_trigger_skipped(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)
            future = now_utc + timedelta(hours=1)
            await _make_event_with_trigger(
                session,
                owner=u,
                title="later",
                dtstart=future,
                next_fire_at=future,
            )

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        touched = await engine.run_once(now_utc=now_utc)
        assert touched == 0
        assert bot.sent == []


class TestEscalation:
    async def test_each_fire_steps_tone_up(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)
            trigger = await _make_event_with_trigger(
                session,
                owner=u,
                title="standup",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )
            trigger_id = trigger.id

        engine = NudgeEngine(session_factory, _StubBot())  # type: ignore[arg-type]

        # First fire: SOFT → NORMAL
        await engine.run_once(now_utc=now_utc)
        async with session_factory() as session:  # type: ignore[operator]
            t = (
                await session.execute(select(Trigger).where(Trigger.id == trigger_id))
            ).scalar_one()
        assert t.current_tone is ToneStep.NORMAL
        assert t.iteration_count == 1

        # Advance time past the new interval and re-fire. SQLite strips
        # tzinfo on round-trip so we re-attach UTC before doing math.
        next_fire = t.next_fire_at
        assert next_fire is not None
        if next_fire.tzinfo is None:
            next_fire = next_fire.replace(tzinfo=UTC)
        await engine.run_once(now_utc=next_fire + timedelta(seconds=1))
        async with session_factory() as session:  # type: ignore[operator]
            t = (
                await session.execute(select(Trigger).where(Trigger.id == trigger_id))
            ).scalar_one()
        assert t.current_tone is ToneStep.SHARP


class TestQuietHours:
    async def test_quiet_hours_defer_instead_of_send(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            # 02:00 Moscow → quiet (22..9 window)
            now_utc = _moscow_at(2)
            await _make_event_with_trigger(
                session,
                owner=u,
                title="should-not-fire",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        touched = await engine.run_once(now_utc=now_utc)

        # Trigger was touched (deferred) but no message sent and no log.
        assert touched == 1
        assert bot.sent == []
        async with session_factory() as session:  # type: ignore[operator]
            logs = list((await session.execute(select(NudgeLog))).scalars())
            triggers = list((await session.execute(select(Trigger))).scalars())
        assert logs == []
        # next_fire_at pushed to after the quiet window ends
        assert triggers[0].next_fire_at is not None
        # next_fire_at is later than now
        assert (
            triggers[0].next_fire_at > now_utc.replace(tzinfo=None)
            or triggers[0].next_fire_at.replace(tzinfo=UTC) > now_utc
        )


class TestCapEscalation:
    async def test_cap_with_silent_iterations_snoozes_12h_and_resets(
        self, session_factory: object
    ) -> None:
        """At HARD tone with N silent NudgeLogs, the next fire must
        snooze for 12 h and reset tone to SOFT instead of sending."""
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)
            # Seed a trigger already at HARD with iteration_count high.
            trigger = await _make_event_with_trigger(
                session,
                owner=u,
                title="ignored",
                dtstart=now_utc,
                next_fire_at=now_utc,
                current_tone=CAP_TONE,
                iteration_count=4,
            )
            # Seed N silent NudgeLog rows so the cap-guard sees a streak.
            for i in range(CAP_ITERATIONS_WITHOUT_REACTION):
                session.add(
                    NudgeLog(
                        trigger_id=trigger.id,
                        fired_at=now_utc - timedelta(minutes=10 * (i + 1)),
                        tone_used=CAP_TONE,
                        voice_profile_used=u.voice_profile,
                        message_text=f"hard {i}",
                        user_reaction=None,
                    )
                )
            await session.commit()
            trigger_id = trigger.id

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        await engine.run_once(now_utc=now_utc)

        # No new message sent; trigger snoozed; tone reset; counter cleared.
        assert bot.sent == []
        async with session_factory() as session:  # type: ignore[operator]
            t = (
                await session.execute(select(Trigger).where(Trigger.id == trigger_id))
            ).scalar_one()
        assert t.state is TriggerState.SNOOZED
        assert t.current_tone is ToneStep.SOFT
        assert t.iteration_count == 0
        assert t.last_cap_snooze_at is not None
        # Compare wall-clock (SQLite strips tz).
        actual_naive = t.next_fire_at.replace(tzinfo=None) if t.next_fire_at else None
        expected_naive = (now_utc + CAP_SNOOZE).replace(tzinfo=None)
        assert actual_naive == expected_naive

    async def test_cap_with_recent_reaction_still_fires(self, session_factory: object) -> None:
        """HARD tone + recent ACK → don't snooze, keep firing."""
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)
            trigger = await _make_event_with_trigger(
                session,
                owner=u,
                title="acked",
                dtstart=now_utc,
                next_fire_at=now_utc,
                current_tone=CAP_TONE,
                iteration_count=4,
            )
            # 3 logs but the LAST one has ACK reaction.
            for i in range(CAP_ITERATIONS_WITHOUT_REACTION - 1):
                session.add(
                    NudgeLog(
                        trigger_id=trigger.id,
                        fired_at=now_utc - timedelta(minutes=10 * (i + 2)),
                        tone_used=CAP_TONE,
                        voice_profile_used=u.voice_profile,
                        message_text=f"hard {i}",
                        user_reaction=None,
                    )
                )
            session.add(
                NudgeLog(
                    trigger_id=trigger.id,
                    fired_at=now_utc - timedelta(minutes=1),
                    tone_used=CAP_TONE,
                    voice_profile_used=u.voice_profile,
                    message_text="acked",
                    user_reaction=NudgeReaction.ACK,
                )
            )
            await session.commit()

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        await engine.run_once(now_utc=now_utc)

        # ACK breaks the silent streak → normal fire happens.
        assert len(bot.sent) == 1


class TestCrossUserIsolation:
    async def test_each_users_trigger_sent_only_to_them(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            alice = await _make_user(session, tg_id=1001, ical_token="alice")
            bob = await _make_user(session, tg_id=2002, ical_token="bob")
            now_utc = _moscow_at(12)
            await _make_event_with_trigger(
                session,
                owner=alice,
                title="alice-task",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )
            await _make_event_with_trigger(
                session,
                owner=bob,
                title="bob-task",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        await engine.run_once(now_utc=now_utc)

        # Two distinct chats, each got their own task title.
        by_chat = dict(bot.sent)
        assert alice.tg_id in by_chat
        assert bob.tg_id in by_chat
        assert "alice-task" in by_chat[alice.tg_id]
        assert "bob-task" in by_chat[bob.tg_id]
        # And bob's title doesn't leak into alice's message.
        assert "bob-task" not in by_chat[alice.tg_id]
        assert "alice-task" not in by_chat[bob.tg_id]


class TestInactiveEvent:
    async def test_inactive_event_marked_done_no_fire(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1001, ical_token="t1")
            now_utc = _moscow_at(12)
            trigger = await _make_event_with_trigger(
                session,
                owner=u,
                title="dead",
                dtstart=now_utc,
                next_fire_at=now_utc,
            )
            # mark event inactive
            event = (
                await session.execute(select(Event).where(Event.id == trigger.event_id))
            ).scalar_one()
            event.is_active = False
            await session.commit()
            trigger_id = trigger.id

        bot = _StubBot()
        engine = NudgeEngine(session_factory, bot)  # type: ignore[arg-type]
        await engine.run_once(now_utc=now_utc)

        assert bot.sent == []
        async with session_factory() as session:  # type: ignore[operator]
            t = (
                await session.execute(select(Trigger).where(Trigger.id == trigger_id))
            ).scalar_one()
        assert t.state is TriggerState.DONE
