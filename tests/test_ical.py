# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""iCal feed + render tests.

Two layers:

- render_calendar() is a pure function over Event rows → exercise it
  directly to verify VEVENT shape, UID format, rrule passthrough.
- /ical/{token} endpoint is exercised through FastAPI TestClient with
  the real app (in-memory SQLite). Cross-user isolation = the most
  important invariant: presenting Bob's token MUST NOT return Alice's
  events, and vice versa.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from icalendar import Calendar
from sqlalchemy import select

from callendulla.api import create_app
from callendulla.api.ical_render import PRODID, render_calendar
from callendulla.config import get_settings
from callendulla.db import Base
from callendulla.db.models import Event, User
from callendulla.db.repositories import EventRepository
from callendulla.db.session import create_engine, create_session_factory, get_session

_FERNET_KEY = Fernet.generate_key().decode()
_SECRET_32 = "a" * 32


def _env() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE00000000",
        "OWNER_TG_ID": "42",
        "LLM_PROVIDER": "gemini",
        "LLM_API_KEY": "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE000",
        "SECRET_KEY": _SECRET_32,
        "DIARY_ENCRYPTION_KEY": _FERNET_KEY,
        "ALLOWED_HOSTS": "*",
        "WEB_BASE_URL": "https://callendulla.example.com",
    }


@pytest.fixture
async def session_factory() -> AsyncIterator[object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for k in (*_env(), "AGPL_SOURCE_URL", "CORS_ORIGINS", "BOT_MODE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir("/tmp")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_user_with_events(
    session_factory: object,
    *,
    tg_id: int,
    ical_token: str,
    titles: list[str],
) -> User:
    async with session_factory() as session:  # type: ignore[operator]
        user = User(
            tg_id=tg_id,
            ical_token=ical_token,
            timezone="Europe/Moscow",
        )
        session.add(user)
        await session.flush()
        repo = EventRepository(session)
        base = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
        for i, title in enumerate(titles):
            await repo.create(
                owner_user_id=user.id,
                title=title,
                dtstart=base + timedelta(hours=i),
            )
        await session.refresh(user)
        return user


# ─── render_calendar — pure-function tests ─────────────────────────


class TestRenderCalendar:
    async def test_includes_prodid_and_version(self, session_factory: object) -> None:
        user = await _seed_user_with_events(session_factory, tg_id=1, ical_token="t", titles=["A"])
        async with session_factory() as session:  # type: ignore[operator]
            events = list((await session.execute(select(Event))).scalars())

        body = render_calendar(events, user_id=user.id)
        cal = Calendar.from_ical(body)
        assert PRODID in str(cal["prodid"])
        assert str(cal["version"]) == "2.0"

    async def test_one_vevent_per_event(self, session_factory: object) -> None:
        user = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="t", titles=["A", "B", "C"]
        )
        async with session_factory() as session:  # type: ignore[operator]
            events = list((await session.execute(select(Event))).scalars())

        body = render_calendar(events, user_id=user.id)
        cal = Calendar.from_ical(body)
        vevents = list(cal.walk("vevent"))
        assert len(vevents) == 3

    async def test_uid_format_contains_user_id_and_event_id(self, session_factory: object) -> None:
        user = await _seed_user_with_events(session_factory, tg_id=1, ical_token="t", titles=["X"])
        async with session_factory() as session:  # type: ignore[operator]
            events = list((await session.execute(select(Event))).scalars())

        body = render_calendar(events, user_id=user.id)
        cal = Calendar.from_ical(body)
        ve = cal.walk("vevent")[0]
        uid = str(ve["uid"])
        assert f"callendulla-{user.id}-{events[0].id}@" in uid

    async def test_summary_carries_title(self, session_factory: object) -> None:
        user = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="t", titles=["Стендап"]
        )
        async with session_factory() as session:  # type: ignore[operator]
            events = list((await session.execute(select(Event))).scalars())

        body = render_calendar(events, user_id=user.id)
        cal = Calendar.from_ical(body)
        ve = cal.walk("vevent")[0]
        assert str(ve["summary"]) == "Стендап"


# ─── /ical/{token} endpoint ─────────────────────────────────────────


class _SessionOverrideClient:
    """TestClient that swaps get_session for our in-memory factory."""

    def __init__(self, app: object, session_factory: object) -> None:
        self._session_factory = session_factory
        app.dependency_overrides[get_session] = self._override  # type: ignore[attr-defined]
        self.client = TestClient(app)  # type: ignore[arg-type]

    async def _override(self) -> AsyncIterator[object]:
        async with self._session_factory() as session:  # type: ignore[operator]
            yield session


@pytest.fixture
async def client_with_db(
    env: None,
    session_factory: object,
) -> AsyncIterator[TestClient]:
    # The lifespan tries to set up loguru — fine. We swap get_session.
    app = create_app()
    helper = _SessionOverrideClient(app, session_factory)
    with helper.client as c:
        yield c


class TestICalEndpoint:
    async def test_valid_token_returns_calendar(
        self, client_with_db: TestClient, session_factory: object
    ) -> None:
        user = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="alice-token", titles=["A"]
        )
        r = client_with_db.get(f"/ical/{user.ical_token}")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/calendar")
        # Parses as valid iCal
        cal = Calendar.from_ical(r.content)
        vevents = list(cal.walk("vevent"))
        assert len(vevents) == 1
        assert str(vevents[0]["summary"]) == "A"

    async def test_unknown_token_returns_404(self, client_with_db: TestClient) -> None:
        r = client_with_db.get("/ical/this-token-does-not-exist")
        assert r.status_code == 404

    async def test_inactive_events_excluded(
        self, client_with_db: TestClient, session_factory: object
    ) -> None:
        user = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="t", titles=["keep", "drop"]
        )
        # Mark the second one inactive
        async with session_factory() as session:  # type: ignore[operator]
            events = list((await session.execute(select(Event).order_by(Event.id))).scalars())
            events[1].is_active = False
            await session.commit()

        r = client_with_db.get(f"/ical/{user.ical_token}")
        assert r.status_code == 200
        cal = Calendar.from_ical(r.content)
        summaries = [str(c["summary"]) for c in cal.walk("vevent")]
        assert summaries == ["keep"]


class TestCrossUserIsolation:
    """Two users, two tokens — each token returns only its own events."""

    async def test_alice_token_no_bob_events(
        self, client_with_db: TestClient, session_factory: object
    ) -> None:
        alice = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="alice-token", titles=["alice-1"]
        )
        bob = await _seed_user_with_events(
            session_factory, tg_id=2, ical_token="bob-token", titles=["bob-secret"]
        )

        r_alice = client_with_db.get(f"/ical/{alice.ical_token}")
        assert r_alice.status_code == 200
        body_alice = r_alice.content.decode("utf-8")
        assert "alice-1" in body_alice
        assert "bob-secret" not in body_alice  # the load-bearing assertion

        r_bob = client_with_db.get(f"/ical/{bob.ical_token}")
        body_bob = r_bob.content.decode("utf-8")
        assert "bob-secret" in body_bob
        assert "alice-1" not in body_bob


class TestRotateToken:
    """Direct DB mutation of ical_token — old token 404s, new token works."""

    async def test_old_token_breaks_new_works(
        self, client_with_db: TestClient, session_factory: object
    ) -> None:
        user = await _seed_user_with_events(
            session_factory, tg_id=1, ical_token="old-token", titles=["X"]
        )
        # Confirm old works
        assert client_with_db.get(f"/ical/{user.ical_token}").status_code == 200

        # Rotate
        async with session_factory() as session:  # type: ignore[operator]
            fresh = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
            fresh.ical_token = "new-token"
            await session.commit()

        assert client_with_db.get("/ical/old-token").status_code == 404
        r = client_with_db.get("/ical/new-token")
        assert r.status_code == 200


# Unused fixture suppression in stubs
_ = patch
