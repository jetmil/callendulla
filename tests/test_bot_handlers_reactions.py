# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Reaction-button callback handler tests.

Covers:
- ack → Trigger state DONE, next_fire_at cleared
- snooze_1h → state PENDING, next_fire_at = now + 1h
- snooze_tomorrow → state PENDING, next_fire_at >= owner's quiet end
- silent_12h → state SNOOZED, next_fire_at = now + 12h
- malformed callback_data → silently dismissed
- cross-user: another user's nudge_log_id → "not for you" + no mutation
- idempotency: clicking twice → no double-write
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from callendulla.bot.handlers.reactions import handle_nudge_reaction
from callendulla.bot.keyboards import NudgeAction, build_callback_data
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


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _seed(session_factory: object) -> tuple[User, Trigger, NudgeLog]:
    async with session_factory() as session:  # type: ignore[operator]
        user = User(
            tg_id=1001,
            ical_token="t1",
            timezone="Europe/Moscow",
            voice_profile=VoiceProfile.WARM_SISTER,
        )
        session.add(user)
        await session.flush()
        event = Event(
            owner_user_id=user.id,
            title="event",
            dtstart=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            timezone="Europe/Moscow",
        )
        session.add(event)
        await session.flush()
        trigger = Trigger(
            event_id=event.id,
            kind=TriggerKind.ONESHOT,
            state=TriggerState.PENDING,
            schedule_spec="x",
            next_fire_at=datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
        )
        session.add(trigger)
        await session.flush()
        log = NudgeLog(
            trigger_id=trigger.id,
            fired_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
            tone_used=ToneStep.SOFT,
            voice_profile_used=user.voice_profile,
            message_text="reminder",
        )
        session.add(log)
        await session.commit()
        await session.refresh(user)
        await session.refresh(trigger)
        await session.refresh(log)
        return user, trigger, log


def _mock_cq(callback_data: str) -> MagicMock:
    cq = MagicMock()
    cq.data = callback_data
    cq.answer = AsyncMock()
    return cq


async def _get_log_and_trigger(
    session_factory: object, log_id: int, trigger_id: int
) -> tuple[NudgeLog, Trigger]:
    async with session_factory() as session:  # type: ignore[operator]
        log = (await session.execute(select(NudgeLog).where(NudgeLog.id == log_id))).scalar_one()
        trig = (await session.execute(select(Trigger).where(Trigger.id == trigger_id))).scalar_one()
        return log, trig


class TestAck:
    async def test_marks_trigger_done(self, session_factory: object) -> None:
        user, trigger, log = await _seed(session_factory)
        cq = _mock_cq(build_callback_data(log.id, NudgeAction.ACK))
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]
        cq.answer.assert_awaited_once()

        new_log, new_trig = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        assert new_log.user_reaction is NudgeReaction.ACK
        assert new_log.reaction_at is not None
        assert new_trig.state is TriggerState.DONE
        assert new_trig.next_fire_at is None


class TestSnooze1h:
    async def test_pushes_one_hour(self, session_factory: object) -> None:
        user, trigger, log = await _seed(session_factory)
        cq = _mock_cq(build_callback_data(log.id, NudgeAction.SNOOZE_1H))
        before = datetime.now(tz=UTC)
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]
        after = datetime.now(tz=UTC)

        _, new_trig = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        assert new_trig.state is TriggerState.PENDING
        # SQLite strips tz on round-trip; compare wall clock
        nfa = new_trig.next_fire_at
        assert nfa is not None
        if nfa.tzinfo is None:
            nfa = nfa.replace(tzinfo=UTC)
        # Should be ~ +1h from "now during the test"
        assert before + timedelta(minutes=59) <= nfa <= after + timedelta(minutes=61)


class TestSnoozeTomorrow:
    async def test_targets_next_quiet_end(self, session_factory: object) -> None:
        user, trigger, log = await _seed(session_factory)
        cq = _mock_cq(build_callback_data(log.id, NudgeAction.SNOOZE_TOMORROW))
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]

        _, new_trig = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        assert new_trig.state is TriggerState.PENDING
        nfa = new_trig.next_fire_at
        assert nfa is not None
        # The user's quiet window is the default 22..9; tomorrow target
        # is local 09:00 + 0..30 min jitter. Convert to local and assert
        # the hour.
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        if nfa.tzinfo is None:
            nfa = nfa.replace(tzinfo=UTC)
        local = nfa.astimezone(ZoneInfo(user.timezone))
        assert local.hour == 9
        assert 0 <= local.minute <= 30


class TestSilent12h:
    async def test_state_snoozed(self, session_factory: object) -> None:
        user, trigger, log = await _seed(session_factory)
        cq = _mock_cq(build_callback_data(log.id, NudgeAction.SILENT_12H))
        before = datetime.now(tz=UTC)
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]
        after = datetime.now(tz=UTC)

        _, new_trig = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        assert new_trig.state is TriggerState.SNOOZED
        nfa = new_trig.next_fire_at
        assert nfa is not None
        if nfa.tzinfo is None:
            nfa = nfa.replace(tzinfo=UTC)
        assert before + timedelta(hours=11, minutes=59) <= nfa
        assert nfa <= after + timedelta(hours=12, minutes=1)


class TestMalformedCallback:
    async def test_garbage_data_dismissed(self, session_factory: object) -> None:
        user, _, _ = await _seed(session_factory)
        cq = _mock_cq("not-a-nudge-callback")
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]
        # answer called silently (no error popup)
        cq.answer.assert_awaited_once()


class TestUnregistered:
    async def test_unregistered_user_refused(self, session_factory: object) -> None:
        cq = _mock_cq(build_callback_data(1, NudgeAction.ACK))
        await handle_nudge_reaction(cq, None, session_factory)  # type: ignore[arg-type]
        cq.answer.assert_awaited_once()
        # Hint message about /start
        text = cq.answer.await_args.args[0]
        assert "start" in text.lower()


class TestCrossUser:
    async def test_other_users_nudge_id_refused(self, session_factory: object) -> None:
        """Eve guesses Alice's nudge_log_id. She gets the same refusal
        message as 'doesn't exist' — no leak of ID existence, no
        mutation on Alice's data."""
        _alice, alice_trigger, alice_log = await _seed(session_factory)
        async with session_factory() as session:  # type: ignore[operator]
            eve = User(tg_id=2002, ical_token="t_eve", timezone="Europe/Moscow")
            session.add(eve)
            await session.commit()
            await session.refresh(eve)

        cq = _mock_cq(build_callback_data(alice_log.id, NudgeAction.ACK))
        await handle_nudge_reaction(cq, eve, session_factory)  # type: ignore[arg-type]

        # Alice's data untouched.
        new_log, new_trig = await _get_log_and_trigger(
            session_factory, alice_log.id, alice_trigger.id
        )
        assert new_log.user_reaction is None
        assert new_trig.state is TriggerState.PENDING
        # Eve got an answer (refusal), not an error.
        cq.answer.assert_awaited_once()

    async def test_nonexistent_nudge_id_refused(self, session_factory: object) -> None:
        user, _, _ = await _seed(session_factory)
        cq = _mock_cq(build_callback_data(99999, NudgeAction.ACK))
        await handle_nudge_reaction(cq, user, session_factory)  # type: ignore[arg-type]
        cq.answer.assert_awaited_once()


class TestIdempotent:
    async def test_double_click_does_not_double_write(self, session_factory: object) -> None:
        user, trigger, log = await _seed(session_factory)
        cq1 = _mock_cq(build_callback_data(log.id, NudgeAction.SNOOZE_1H))
        await handle_nudge_reaction(cq1, user, session_factory)  # type: ignore[arg-type]
        _, trig_after_first = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        first_nfa = trig_after_first.next_fire_at
        assert first_nfa is not None

        # Second click should NOT push next_fire_at by another hour.
        cq2 = _mock_cq(build_callback_data(log.id, NudgeAction.ACK))
        await handle_nudge_reaction(cq2, user, session_factory)  # type: ignore[arg-type]

        new_log, trig_after_second = await _get_log_and_trigger(session_factory, log.id, trigger.id)
        # Reaction stays at the FIRST choice.
        assert new_log.user_reaction is NudgeReaction.SNOOZE_1H
        # And the trigger state is what the first click set it to.
        assert trig_after_second.state is TriggerState.PENDING
        assert trig_after_second.next_fire_at == first_nfa
