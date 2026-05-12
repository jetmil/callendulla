# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""``EventRepository`` tests — focus on cross-user isolation.

These run against in-memory SQLite. Cross-user isolation is the most
important invariant: a missing owner filter at the call site must not
let the repository surface another user's events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from callendulla.db import Base
from callendulla.db.models import Trigger, User
from callendulla.db.repositories import EventRepository
from callendulla.db.session import create_engine, create_session_factory


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _make_user(session: AsyncSession, *, tg_id: int, ical_token: str) -> User:
    user = User(tg_id=tg_id, ical_token=ical_token)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class TestCreate:
    async def test_creates_event_and_default_trigger(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            user = await _make_user(session, tg_id=42, ical_token="t1")

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            dtstart = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
            event = await repo.create(
                owner_user_id=user.id,
                title="standup",
                dtstart=dtstart,
            )

        assert event.id is not None
        assert event.title == "standup"
        assert event.is_active is True

        # Default OneShot trigger created on dtstart.
        async with session_factory() as session:  # type: ignore[operator]
            from sqlalchemy import select  # noqa: PLC0415

            triggers = list(
                (
                    await session.execute(select(Trigger).where(Trigger.event_id == event.id))
                ).scalars()
            )
        assert len(triggers) == 1
        # SQLite strips tzinfo on round-trip (no TIMESTAMP WITH TIME ZONE).
        # Postgres preserves it. Compare wall-clock components only —
        # tz preservation is a Postgres-specific integration concern.
        stored = triggers[0].next_fire_at
        assert stored.replace(tzinfo=None) == dtstart.replace(tzinfo=None)


class TestListForOwner:
    async def test_returns_only_own_events(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            alice = await _make_user(session, tg_id=1, ical_token="alice")
            bob = await _make_user(session, tg_id=2, ical_token="bob")

        base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            await repo.create(owner_user_id=alice.id, title="alice-1", dtstart=base)
            await repo.create(
                owner_user_id=alice.id,
                title="alice-2",
                dtstart=base + timedelta(hours=1),
            )
            await repo.create(owner_user_id=bob.id, title="bob-1", dtstart=base)

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            alice_events = await repo.list_for_owner(alice.id)
            bob_events = await repo.list_for_owner(bob.id)

        alice_titles = {e.title for e in alice_events}
        bob_titles = {e.title for e in bob_events}
        assert alice_titles == {"alice-1", "alice-2"}
        assert bob_titles == {"bob-1"}
        # Belt and braces: no leakage at the title level.
        assert "bob-1" not in alice_titles
        assert "alice-1" not in bob_titles

    async def test_ordered_by_dtstart_asc(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            await repo.create(owner_user_id=u.id, title="later", dtstart=base + timedelta(hours=2))
            await repo.create(owner_user_id=u.id, title="sooner", dtstart=base)
            await repo.create(owner_user_id=u.id, title="middle", dtstart=base + timedelta(hours=1))

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            events = await repo.list_for_owner(u.id)

        assert [e.title for e in events] == ["sooner", "middle", "later"]

    async def test_respects_limit(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            for i in range(15):
                await repo.create(
                    owner_user_id=u.id, title=f"e{i}", dtstart=base + timedelta(hours=i)
                )

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            events = await repo.list_for_owner(u.id, limit=5)
        assert len(events) == 5


class TestGetForOwner:
    async def test_returns_own_event(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            created = await repo.create(
                owner_user_id=u.id,
                title="t",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            fetched = await repo.get_for_owner(u.id, created.id)
        assert fetched.id == created.id

    async def test_other_users_event_raises(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            alice = await _make_user(session, tg_id=1, ical_token="alice")
            bob = await _make_user(session, tg_id=2, ical_token="bob")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            alice_event = await repo.create(
                owner_user_id=alice.id,
                title="alice-secret",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            with pytest.raises(NoResultFound):
                await repo.get_for_owner(bob.id, alice_event.id)

    async def test_missing_event_raises(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            with pytest.raises(NoResultFound):
                await repo.get_for_owner(u.id, 9999)


class TestDeleteForOwner:
    async def test_deletes_own_event(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            event = await repo.create(
                owner_user_id=u.id,
                title="t",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            deleted = await repo.delete_for_owner(u.id, event.id)
        assert deleted is True

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            with pytest.raises(NoResultFound):
                await repo.get_for_owner(u.id, event.id)

    async def test_other_users_event_returns_false(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            alice = await _make_user(session, tg_id=1, ical_token="alice")
            bob = await _make_user(session, tg_id=2, ical_token="bob")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            alice_event = await repo.create(
                owner_user_id=alice.id,
                title="alice-event",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            deleted = await repo.delete_for_owner(bob.id, alice_event.id)
        assert deleted is False

        # Alice's event is still there.
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            still_there = await repo.get_for_owner(alice.id, alice_event.id)
        assert still_there.id == alice_event.id


class TestCountForOwner:
    async def test_zero_when_empty(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            u = await _make_user(session, tg_id=1, ical_token="u")
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            assert await repo.count_for_owner(u.id) == 0

    async def test_counts_only_own(self, session_factory: object) -> None:
        async with session_factory() as session:  # type: ignore[operator]
            alice = await _make_user(session, tg_id=1, ical_token="alice")
            bob = await _make_user(session, tg_id=2, ical_token="bob")
        base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            for i in range(3):
                await repo.create(
                    owner_user_id=alice.id, title=f"a{i}", dtstart=base + timedelta(hours=i)
                )
            await repo.create(owner_user_id=bob.id, title="b", dtstart=base)

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            assert await repo.count_for_owner(alice.id) == 3
            assert await repo.count_for_owner(bob.id) == 1
