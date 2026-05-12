# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``/forget`` flow tests.

Verifies:
- /forget without registration → benign refusal
- /forget with registration → confirmation message with keyboard
- Cancel callback → no DB mutation
- Confirm callback → user + ALL related rows deleted (cascade)
- nudge_cache is NOT affected (no FK to users; cross-user-safe by
  design)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select

from callendulla.bot.handlers.forget import (
    handle_forget,
    handle_forget_cancel,
    handle_forget_confirm,
)
from callendulla.bot.keyboards import (
    FORGET_CANCEL_CALLBACK,
    FORGET_CONFIRM_CALLBACK,
)
from callendulla.core.voice_crypto import encrypt
from callendulla.db import Base
from callendulla.db.models import (
    Event,
    NudgeCache,
    NudgeLog,
    ToneStep,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
    VoiceDiary,
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


@pytest.fixture
def fernet_key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


async def _seed_full_user(session_factory: object, fernet_key: SecretStr) -> User:
    """Create a user with one of every kind of related row."""
    async with session_factory() as session:  # type: ignore[operator]
        user = User(tg_id=1001, ical_token="alice-tok", timezone="Europe/Moscow")
        session.add(user)
        await session.flush()

        event = Event(
            owner_user_id=user.id,
            title="standup",
            dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            timezone="Europe/Moscow",
        )
        session.add(event)
        await session.flush()

        trigger = Trigger(
            event_id=event.id,
            kind=TriggerKind.ONESHOT,
            state=TriggerState.PENDING,
            schedule_spec="x",
            next_fire_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        )
        session.add(trigger)
        await session.flush()

        session.add(
            NudgeLog(
                trigger_id=trigger.id,
                fired_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
                tone_used=ToneStep.SOFT,
                voice_profile_used=VoiceProfile.WARM_SISTER,
                message_text="ping",
            )
        )
        session.add(
            VoiceDiary(
                owner_user_id=user.id,
                audio_ciphertext=encrypt(b"audio", key=fernet_key),
                transcript_ciphertext=encrypt(b"", key=fernet_key),
            )
        )
        # Unrelated cache row — should survive the forget cascade.
        session.add(
            NudgeCache(
                cache_key="orphan-cache-key",
                response_text="cached llm reply",
                response_meta={},
            )
        )
        await session.commit()
        await session.refresh(user)
        return user


def _mock_message() -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    return msg


def _mock_callback(data: str, with_message: bool = True) -> MagicMock:
    cq = MagicMock()
    cq.data = data
    cq.answer = AsyncMock()
    if with_message:
        # The callback's source message — we attempt to edit it.
        cq.message = MagicMock(spec=["edit_text"])
        cq.message.edit_text = AsyncMock()
    else:
        cq.message = None
    return cq


# ── /forget command itself ──────────────────────────────────────────


class TestForgetCommand:
    async def test_unregistered_user_benign(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_forget(msg, None)
        text = msg.answer.await_args.args[0]
        assert "не пробовал" in text or "нет" in text.lower()
        # No keyboard was attached
        kwargs = msg.answer.await_args.kwargs
        assert "reply_markup" not in kwargs

    async def test_registered_user_gets_confirmation(
        self, session_factory: object, fernet_key: SecretStr
    ) -> None:
        user = await _seed_full_user(session_factory, fernet_key)
        msg = _mock_message()
        await handle_forget(msg, user)
        text = msg.answer.await_args.args[0]
        assert "удалить" in text.lower()
        # Keyboard attached with two buttons
        kwargs = msg.answer.await_args.kwargs
        kb = kwargs["reply_markup"]
        flat = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = {btn.callback_data for btn in flat}
        assert callbacks == {FORGET_CANCEL_CALLBACK, FORGET_CONFIRM_CALLBACK}


# ── Cancel ──────────────────────────────────────────────────────────


class TestForgetCancel:
    async def test_cancel_does_not_delete(
        self, session_factory: object, fernet_key: SecretStr
    ) -> None:
        user = await _seed_full_user(session_factory, fernet_key)
        cq = _mock_callback(FORGET_CANCEL_CALLBACK)
        await handle_forget_cancel(cq, user)
        cq.answer.assert_awaited_once()

        async with session_factory() as session:  # type: ignore[operator]
            still_there = (
                await session.execute(select(User).where(User.id == user.id))
            ).scalar_one_or_none()
        assert still_there is not None


# ── Confirm: the cascade ────────────────────────────────────────────


class TestForgetConfirm:
    async def test_unregistered_caller_benign(self, session_factory: object) -> None:
        cq = _mock_callback(FORGET_CONFIRM_CALLBACK)
        await handle_forget_confirm(cq, None, session_factory)  # type: ignore[arg-type]
        cq.answer.assert_awaited_once()

    async def test_deletes_user_and_all_related(
        self, session_factory: object, fernet_key: SecretStr
    ) -> None:
        user = await _seed_full_user(session_factory, fernet_key)
        cq = _mock_callback(FORGET_CONFIRM_CALLBACK)
        await handle_forget_confirm(cq, user, session_factory)  # type: ignore[arg-type]

        async with session_factory() as session:  # type: ignore[operator]
            users = list((await session.execute(select(User))).scalars())
            events = list((await session.execute(select(Event))).scalars())
            triggers = list((await session.execute(select(Trigger))).scalars())
            nudges = list((await session.execute(select(NudgeLog))).scalars())
            diary = list((await session.execute(select(VoiceDiary))).scalars())
            cache = list((await session.execute(select(NudgeCache))).scalars())

        assert users == []
        assert events == []
        assert triggers == []
        assert nudges == []
        assert diary == []
        # NudgeCache has NO FK to users — cross-user-safe by design,
        # so the orphan cache row stays.
        assert len(cache) == 1
        assert cache[0].cache_key == "orphan-cache-key"

    async def test_only_calling_user_is_erased(
        self, session_factory: object, fernet_key: SecretStr
    ) -> None:
        """Bob calls /forget — Alice's events MUST survive."""
        alice = await _seed_full_user(session_factory, fernet_key)
        async with session_factory() as session:  # type: ignore[operator]
            bob = User(tg_id=2002, ical_token="bob-tok", timezone="Europe/Moscow")
            session.add(bob)
            await session.flush()
            bob_event = Event(
                owner_user_id=bob.id,
                title="bob-meeting",
                dtstart=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                timezone="Europe/Moscow",
            )
            session.add(bob_event)
            await session.commit()
            await session.refresh(bob)

        cq = _mock_callback(FORGET_CONFIRM_CALLBACK)
        await handle_forget_confirm(cq, bob, session_factory)  # type: ignore[arg-type]

        async with session_factory() as session:  # type: ignore[operator]
            survivors = list((await session.execute(select(User))).scalars())
            survivor_events = list((await session.execute(select(Event))).scalars())
        assert {u.id for u in survivors} == {alice.id}
        assert {e.owner_user_id for e in survivor_events} == {alice.id}
