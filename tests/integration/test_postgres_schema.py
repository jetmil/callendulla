# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Postgres-specific schema and behaviour tests.

Covers what SQLite-backed tests cannot:
- ``alembic upgrade head`` on a pristine Postgres
- JSONB column on ``nudge_cache.response_meta``
- ``TIMESTAMP WITH TIME ZONE`` round-trip preserves tzinfo
- CHECK constraint on user quiet hours actually rejects bad values
- FK CASCADE works without the SQLite PRAGMA workaround
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callendulla.db.models import (
    Event,
    NudgeCache,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
)


class TestSchemaExists:
    async def test_all_tables_created(self, fresh_db: AsyncSession) -> None:
        result = await fresh_db.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        tables = {row[0] for row in result}
        # alembic_version + the 6 callendulla tables
        assert {
            "alembic_version",
            "users",
            "events",
            "triggers",
            "nudge_logs",
            "nudge_cache",
            "voice_diary",
        } <= tables

    async def test_alembic_version_is_initial(self, fresh_db: AsyncSession) -> None:
        result = await fresh_db.execute(text("SELECT version_num FROM alembic_version"))
        rows = list(result)
        assert len(rows) == 1
        assert rows[0][0] == "0001_initial_schema"


class TestJSONBColumn:
    async def test_response_meta_is_jsonb(self, fresh_db: AsyncSession) -> None:
        """SQLite falls back to TEXT; Postgres must be true JSONB."""
        result = await fresh_db.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'nudge_cache' AND column_name = 'response_meta'"
            )
        )
        data_type = result.scalar_one()
        assert data_type == "jsonb"

    async def test_jsonb_round_trip_preserves_structure(self, fresh_db: AsyncSession) -> None:
        meta = {"profile": "warm_sister", "tone": "soft", "tokens_in": 120, "stale": False}
        row = NudgeCache(cache_key="abc-roundtrip", response_text="hi", response_meta=meta)
        fresh_db.add(row)
        await fresh_db.commit()
        await fresh_db.refresh(row)

        # Reload through a fresh query to confirm DB->Python round-trip
        loaded = (
            await fresh_db.execute(
                select(NudgeCache).where(NudgeCache.cache_key == "abc-roundtrip")
            )
        ).scalar_one()
        assert loaded.response_meta == meta

    async def test_jsonb_path_operator_works(self, fresh_db: AsyncSession) -> None:
        """JSONB-specific operator ``->>`` works — proving it really IS
        JSONB and not text. Catches a future regression that downgrades
        the column to JSON or TEXT."""
        row = NudgeCache(
            cache_key="path-test",
            response_text="x",
            response_meta={"profile": "drill_sergeant"},
        )
        fresh_db.add(row)
        await fresh_db.commit()

        result = await fresh_db.execute(
            text(
                "SELECT cache_key FROM nudge_cache "
                "WHERE response_meta ->> 'profile' = 'drill_sergeant'"
            )
        )
        keys = [r[0] for r in result]
        assert "path-test" in keys


class TestTimezoneRoundTrip:
    async def test_aware_datetime_preserves_offset(self, fresh_db: AsyncSession) -> None:
        """Postgres stores ``TIMESTAMP WITH TIME ZONE`` as UTC internally
        but the round-tripped value MUST come back tz-aware. SQLite
        drops the tz; tests there compare wall-clock components only.
        Here we assert the full thing."""
        msk = ZoneInfo("Europe/Moscow")
        local = datetime(2026, 6, 1, 10, 0, tzinfo=msk)
        user = User(tg_id=1, ical_token="tz-test", timezone="Europe/Moscow")
        fresh_db.add(user)
        await fresh_db.flush()

        event = Event(
            owner_user_id=user.id,
            title="tz-event",
            dtstart=local,
            timezone="Europe/Moscow",
        )
        fresh_db.add(event)
        await fresh_db.commit()
        await fresh_db.refresh(event)

        # Reload to be sure we're not looking at the same Python obj
        loaded = (await fresh_db.execute(select(Event).where(Event.id == event.id))).scalar_one()
        assert loaded.dtstart.tzinfo is not None
        # Postgres returns in UTC; same wall-clock moment, just shifted
        assert loaded.dtstart == local


class TestCheckConstraint:
    async def test_quiet_from_equal_to_rejected(self, fresh_db: AsyncSession) -> None:
        """``ck_users_quiet_hours_valid`` must reject equal hours.
        Default would silence the bot 24h/day."""
        user = User(
            tg_id=1,
            ical_token="ck-equal",
            quiet_from_hour=10,
            quiet_to_hour=10,
        )
        fresh_db.add(user)
        with pytest.raises(IntegrityError):
            await fresh_db.commit()

    async def test_quiet_hour_out_of_range_rejected(self, fresh_db: AsyncSession) -> None:
        await fresh_db.rollback()  # reset from any previous failure in same session
        user = User(
            tg_id=2,
            ical_token="ck-oor",
            quiet_from_hour=25,  # invalid
            quiet_to_hour=9,
        )
        fresh_db.add(user)
        with pytest.raises(IntegrityError):
            await fresh_db.commit()


class TestForeignKeyCascade:
    async def test_user_delete_cascades_without_pragma(self, fresh_db: AsyncSession) -> None:
        """Postgres enforces FK CASCADE by default. SQLite needs
        ``PRAGMA foreign_keys=ON`` which we wire in :mod:`db.session`.
        Here we assert the cascade really happens on the prod target."""
        user = User(tg_id=1, ical_token="cascade", timezone="Europe/Moscow")
        fresh_db.add(user)
        await fresh_db.flush()

        event = Event(
            owner_user_id=user.id,
            title="cascade-event",
            dtstart=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
            timezone="Europe/Moscow",
        )
        fresh_db.add(event)
        await fresh_db.flush()

        trigger = Trigger(
            event_id=event.id,
            kind=TriggerKind.ONESHOT,
            state=TriggerState.PENDING,
            schedule_spec="x",
            next_fire_at=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        )
        fresh_db.add(trigger)
        await fresh_db.commit()

        await fresh_db.delete(user)
        await fresh_db.commit()

        events_left = list((await fresh_db.execute(select(Event))).scalars())
        triggers_left = list((await fresh_db.execute(select(Trigger))).scalars())
        users_left = list((await fresh_db.execute(select(User))).scalars())
        assert events_left == []
        assert triggers_left == []
        assert users_left == []


class TestEnumColumnNotNative:
    """We declared enums with ``native_enum=False`` so they end up as
    VARCHAR + CHECK in Postgres. Catches a future regression that
    changes to ``native_enum=True`` (which would require an explicit
    migration to create the Postgres enum type)."""

    async def test_user_role_column_is_varchar(self, fresh_db: AsyncSession) -> None:
        result = await fresh_db.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'role'"
            )
        )
        data_type = result.scalar_one()
        assert data_type == "character varying"
