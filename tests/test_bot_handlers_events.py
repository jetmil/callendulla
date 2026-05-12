# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Unit tests for ``/add``, ``/list``, ``/delete`` event handlers.

These exercise the parse/answer surface — actual DB writes are
verified by ``tests/test_event_repository.py``. Here we make sure the
handlers reply with sensible text and call the repository correctly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from callendulla.bot.handlers.events import handle_add, handle_delete, handle_list
from callendulla.db import Base
from callendulla.db.models import User
from callendulla.db.session import create_engine, create_session_factory


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def alice(session_factory: object) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        u = User(
            tg_id=1001,
            tg_username="alice",
            ical_token="t_alice",
            timezone="Europe/Moscow",
        )
        session.add(u)
        await session.commit()
        await session.refresh(u)
        return u


def _mock_message() -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    return msg


def _cmd(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="add", args=args)


class TestAddUnregistered:
    async def test_refuses_unregistered(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(msg, None, _cmd("title ; 2026-06-01 10:00"), session_factory)  # type: ignore[arg-type]
        msg.answer.assert_awaited_once()
        text = msg.answer.await_args.args[0]
        assert "/start" in text


class TestAddBadInput:
    async def test_no_args_shows_help(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(msg, alice, _cmd(None), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Формат" in text

    async def test_missing_semicolon_shows_help(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(msg, alice, _cmd("just a title without time"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Формат" in text

    async def test_empty_title_refused(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(msg, alice, _cmd(" ; 2026-06-01 10:00"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Название" in text

    async def test_bad_datetime_refused(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(msg, alice, _cmd("title ; not-a-date"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "не распознал" in text.lower() or "не распознал" in text


class TestAddSuccess:
    async def test_creates_event_and_replies(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(
            msg,
            alice,
            _cmd("Стендап ; 2026-06-01 10:00"),
            session_factory,  # type: ignore[arg-type]
        )
        msg.answer.assert_awaited_once()
        text = msg.answer.await_args.args[0]
        assert "Стендап" in text
        assert "#1" in text  # first event in the DB

    async def test_event_persisted_to_db(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_add(
            msg,
            alice,
            _cmd("Морковка ; 2026-06-02 09:30"),
            session_factory,  # type: ignore[arg-type]
        )
        from sqlalchemy import select  # noqa: PLC0415

        from callendulla.db.models import Event  # noqa: PLC0415

        async with session_factory() as session:  # type: ignore[operator]
            event = (await session.execute(select(Event))).scalar_one()
        assert event.title == "Морковка"
        assert event.owner_user_id == alice.id


class TestListEmpty:
    async def test_says_no_events(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_list(msg, alice, session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "пока нет" in text.lower() or "Формат" in text


class TestListNonEmpty:
    async def test_lists_own_events(self, alice: User, session_factory: object) -> None:
        # seed two events
        async with session_factory() as session:  # type: ignore[operator]
            from callendulla.db.repositories import EventRepository  # noqa: PLC0415

            repo = EventRepository(session)
            from datetime import UTC, datetime  # noqa: PLC0415

            await repo.create(
                owner_user_id=alice.id,
                title="Утром",
                dtstart=datetime(2026, 6, 1, 8, 0, tzinfo=UTC),
            )
            await repo.create(
                owner_user_id=alice.id,
                title="Вечером",
                dtstart=datetime(2026, 6, 1, 18, 0, tzinfo=UTC),
            )

        msg = _mock_message()
        await handle_list(msg, alice, session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Утром" in text
        assert "Вечером" in text


class TestListCrossUserIsolation:
    async def test_does_not_show_other_users_events(self, session_factory: object) -> None:
        from callendulla.db.repositories import EventRepository  # noqa: PLC0415

        async with session_factory() as session:  # type: ignore[operator]
            bob = User(tg_id=2002, ical_token="t_bob", timezone="Europe/Moscow")
            session.add(bob)
            await session.commit()
            await session.refresh(bob)
            from datetime import UTC, datetime  # noqa: PLC0415

            repo = EventRepository(session)
            await repo.create(
                owner_user_id=bob.id,
                title="bob-private-meeting",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )
            # Make a separate user 'alice' so list_for_owner is called with her id
            alice_user = User(tg_id=1001, ical_token="t_alice", timezone="Europe/Moscow")
            session.add(alice_user)
            await session.commit()
            await session.refresh(alice_user)

        msg = _mock_message()
        await handle_list(msg, alice_user, session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "bob-private-meeting" not in text


class TestDelete:
    async def test_refuses_unregistered(self, session_factory: object) -> None:
        msg = _mock_message()
        await handle_delete(msg, None, _cmd("1"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "/start" in text

    async def test_non_numeric_id_rejected(self, alice: User, session_factory: object) -> None:
        msg = _mock_message()
        await handle_delete(msg, alice, _cmd("not-an-id"), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "Формат" in text

    async def test_other_users_event_same_response_as_not_found(
        self, session_factory: object
    ) -> None:
        """Cross-user safety: same message for "not yours" and
        "doesn't exist" — don't leak ID existence."""
        from callendulla.db.repositories import EventRepository  # noqa: PLC0415

        async with session_factory() as session:  # type: ignore[operator]
            bob = User(tg_id=2002, ical_token="t_bob", timezone="Europe/Moscow")
            session.add(bob)
            await session.commit()
            await session.refresh(bob)
            from datetime import UTC, datetime  # noqa: PLC0415

            repo = EventRepository(session)
            bob_event = await repo.create(
                owner_user_id=bob.id,
                title="bob-secret",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )
            alice_user = User(tg_id=1001, ical_token="t_alice", timezone="Europe/Moscow")
            session.add(alice_user)
            await session.commit()
            await session.refresh(alice_user)

        msg = _mock_message()
        await handle_delete(msg, alice_user, _cmd(str(bob_event.id)), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "не найдено" in text.lower()
        assert "bob-secret" not in text

    async def test_deletes_own_event(self, alice: User, session_factory: object) -> None:
        from callendulla.db.repositories import EventRepository  # noqa: PLC0415

        async with session_factory() as session:  # type: ignore[operator]
            repo = EventRepository(session)
            from datetime import UTC, datetime  # noqa: PLC0415

            event = await repo.create(
                owner_user_id=alice.id,
                title="to-delete",
                dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            )

        msg = _mock_message()
        await handle_delete(msg, alice, _cmd(str(event.id)), session_factory)  # type: ignore[arg-type]
        text = msg.answer.await_args.args[0]
        assert "удалено" in text.lower()
        assert "to-delete" in text
