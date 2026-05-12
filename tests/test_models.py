# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Smoke tests for the declarative metadata.

Real CRUD lives in ``tests/integration/`` (testcontainers, real Postgres),
and runs on demand. These tests only verify:

- every model registers against ``Base.metadata``
- expected tables, columns, indexes and constraints exist
- no PII leaks in ``__repr__``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from callendulla.db import Base
from callendulla.db.models import (
    Event,
    NudgeCache,
    NudgeLog,
    NudgeReaction,
    ToneStep,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
    UserRole,
    UserStatus,
    VoiceDiary,
    VoiceProfile,
)
from callendulla.db.session import create_engine, create_session_factory

EXPECTED_TABLES: set[str] = {
    "users",
    "events",
    "triggers",
    "nudge_logs",
    "nudge_cache",
    "voice_diary",
}


class TestSchemaSurface:
    def test_all_tables_registered(self) -> None:
        registered = set(Base.metadata.tables.keys())
        assert registered == EXPECTED_TABLES

    def test_no_unexpected_tables(self) -> None:
        """Catch accidental copy-paste creating a stray model."""
        unexpected = set(Base.metadata.tables.keys()) - EXPECTED_TABLES
        assert not unexpected, f"unknown tables: {unexpected}"

    def test_users_has_unique_tg_id(self) -> None:
        users = Base.metadata.tables["users"]
        tg_id_col = users.columns["tg_id"]
        assert tg_id_col.unique is True

    def test_users_has_unique_ical_token(self) -> None:
        users = Base.metadata.tables["users"]
        assert users.columns["ical_token"].unique is True

    def test_nudge_cache_has_no_user_fk(self) -> None:
        """Cross-tenant safety: cache rows must not link to a user."""
        cache = Base.metadata.tables["nudge_cache"]
        for fk in cache.foreign_keys:
            assert fk.column.table.name != "users", (
                "NudgeCache must remain user-anonymous; see docstring in models.py for the why"
            )

    def test_quiet_hours_check_constraint_present(self) -> None:
        users = Base.metadata.tables["users"]
        constraint_names = {c.name for c in users.constraints if c.name}
        assert "ck_users_quiet_hours_valid" in constraint_names

    def test_trigger_due_index(self) -> None:
        triggers = Base.metadata.tables["triggers"]
        index_names = {idx.name for idx in triggers.indexes}
        assert "ix_trigger_due" in index_names


class TestModelInstantiation:
    """Sanity-check construction without a session — catches typo bugs."""

    def _make_user(self, **overrides: Any) -> User:
        defaults: dict[str, Any] = {
            "tg_id": 1001,
            "tg_username": "alice",
            "ical_token": "tok_alice_secret_must_not_appear_in_repr",
        }
        return User(**(defaults | overrides))

    def test_user_repr_omits_ical_token(self) -> None:
        u = self._make_user(role=UserRole.MEMBER)
        assert "tok_alice_secret_must_not_appear_in_repr" not in repr(u)

    def test_column_defaults_declared(self) -> None:
        """Schema-level defaults (visible only on INSERT) are wired correctly.

        SQLAlchemy 2 does not run ``default=`` at ``__init__`` time —
        the value materialises during flush. Verifying via ``Column.default``
        is the right check; runtime-default-on-construction tests would
        either need a live session or be misleading.
        """
        users = Base.metadata.tables["users"]
        assert users.columns["role"].default.arg is UserRole.MEMBER
        assert users.columns["status"].default.arg is UserStatus.ACTIVE
        assert users.columns["voice_profile"].default.arg is VoiceProfile.WARM_SISTER
        assert users.columns["timezone"].default.arg == "Europe/Moscow"
        assert users.columns["quiet_from_hour"].default.arg == 22
        assert users.columns["quiet_to_hour"].default.arg == 9

    def test_event_chain(self) -> None:
        # Use explicit FK ids to avoid touching unloaded relationship
        # collections; full backref tests live in tests/integration/
        # against a real DB.
        e = Event(
            owner_user_id=1,
            title="standup",
            dtstart=datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
        )
        t = Trigger(
            event=e,
            kind=TriggerKind.ONESHOT,
            schedule_spec="2026-05-12T10:00:00Z",
            state=TriggerState.PENDING,
        )
        n = NudgeLog(
            trigger=t,
            tone_used=ToneStep.SOFT,
            voice_profile_used=VoiceProfile.WARM_SISTER,
            message_text="готовься к стендапу",
            fired_at=datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
        )
        assert t.event is e
        assert n.trigger is t

    def test_nudge_reaction_optional(self) -> None:
        log = NudgeLog(
            trigger_id=1,
            tone_used=ToneStep.HARD,
            voice_profile_used=VoiceProfile.DRILL_SERGEANT,
            message_text="последнее предупреждение",
            fired_at=datetime(2026, 5, 12, 10, 0, tzinfo=UTC),
        )
        assert log.user_reaction is None

    def test_nudge_reaction_enum_values(self) -> None:
        assert NudgeReaction.ACK.value == "ack"
        assert NudgeReaction.SILENT_12H.value == "silent_12h"

    def test_voice_diary_binary(self) -> None:
        u = self._make_user()
        d = VoiceDiary(
            owner=u,
            audio_ciphertext=b"\x00\x01\x02",
            transcript_ciphertext=b"\xff\xfe",
            duration_sec=12.5,
        )
        assert d.audio_ciphertext == b"\x00\x01\x02"

    def test_nudge_cache_repr_truncates_key(self) -> None:
        c = NudgeCache(
            cache_key="a" * 64,
            response_text="...",
            response_meta={},
        )
        text = repr(c)
        # full key would be 64 chars; repr truncates with ellipsis
        assert "a" * 64 not in text
        assert "aaaaaaaa" in text


class TestNamingConvention:
    """Alembic autogenerate relies on deterministic constraint names."""

    def test_foreign_key_naming(self) -> None:
        events = Base.metadata.tables["events"]
        fk_names = {fk.constraint.name for fk in events.foreign_keys if fk.constraint.name}
        assert "fk_events_owner_user_id_users" in fk_names

    def test_index_naming(self) -> None:
        users = Base.metadata.tables["users"]
        index_names = {idx.name for idx in users.indexes}
        # tg_id has index=True and unique=True → autogenerated index
        # follows ix_<col_label> convention
        assert any("tg_id" in name for name in index_names)


class TestSessionFactoryWiring:
    """``create_engine`` / ``create_session_factory`` must accept any DSN."""

    def test_create_engine_with_sqlite_memory(self) -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        try:
            assert engine.dialect.name == "sqlite"
        finally:
            # AsyncEngine.dispose() is async; sync release is fine for test.
            engine.sync_engine.dispose()

    def test_create_session_factory_from_engine(self) -> None:
        engine = create_engine("sqlite+aiosqlite:///:memory:")
        try:
            factory = create_session_factory(engine)
            # don't actually run an async session here; just ensure the
            # factory builds without errors.
            assert callable(factory)
        finally:
            engine.sync_engine.dispose()
