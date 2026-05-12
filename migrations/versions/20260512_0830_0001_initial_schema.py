# SPDX-License-Identifier: AGPL-3.0-or-later
"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-12 08:30:00.000000

Creates the six tables from ``callendulla.db.models``: users, events,
triggers, nudge_logs, nudge_cache, voice_diary. Constraint names follow
the project naming convention so future autogenerate diffs stay clean.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Enum value sets — declared once for readability, not re-imported from
# the model module because that would couple migration semantics to
# python-side renames. Migrations are immutable history.
_USER_ROLE = ("owner", "member")
_USER_STATUS = ("active", "pending_invite", "disabled")
_VOICE_PROFILE = (
    "brutal_bro",
    "warm_sister",
    "office_neutral",
    "drill_sergeant",
    "iron_lady",
    "quiet_mentor",
)
_TRIGGER_KIND = ("oneshot", "interval", "cron")
_TRIGGER_STATE = ("pending", "firing", "snoozed", "done", "disabled")
_TONE_STEP = ("soft", "normal", "sharp", "hard")
_NUDGE_REACTION = ("ack", "snooze_1h", "snooze_tomorrow", "silent_12h")


def upgrade() -> None:
    # ─── users ─────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_username", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column(
            "role",
            sa.Enum(*_USER_ROLE, name="user_role", native_enum=False, length=16),
            nullable=False,
            server_default="member",
        ),
        sa.Column(
            "status",
            sa.Enum(*_USER_STATUS, name="user_status", native_enum=False, length=24),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "voice_profile",
            sa.Enum(*_VOICE_PROFILE, name="voice_profile", native_enum=False, length=32),
            nullable=False,
            server_default="warm_sister",
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/Moscow",
        ),
        sa.Column("quiet_from_hour", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("quiet_to_hour", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("ical_token", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "quiet_from_hour BETWEEN 0 AND 23 AND quiet_to_hour BETWEEN 0 AND 23 "
            "AND quiet_from_hour <> quiet_to_hour",
            name="ck_users_quiet_hours_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("tg_id", name="uq_users_tg_id"),
        sa.UniqueConstraint("ical_token", name="uq_users_ical_token"),
    )
    op.create_index("ix_users_tg_id", "users", ["tg_id"], unique=False)
    op.create_index("ix_users_ical_token", "users", ["ical_token"], unique=False)

    # ─── events ────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("dtstart", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dtend", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "timezone",
            sa.String(length=64),
            nullable=False,
            server_default="Europe/Moscow",
        ),
        sa.Column("rrule", sa.String(length=512), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_events_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )
    op.create_index("ix_events_owner_user_id", "events", ["owner_user_id"], unique=False)

    # ─── triggers ──────────────────────────────────────────────────
    op.create_table(
        "triggers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(*_TRIGGER_KIND, name="trigger_kind", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(*_TRIGGER_STATE, name="trigger_state", native_enum=False, length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("schedule_spec", sa.String(length=256), nullable=False),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "current_tone",
            sa.Enum(*_TONE_STEP, name="tone_step", native_enum=False, length=12),
            nullable=False,
            server_default="soft",
        ),
        sa.Column(
            "iteration_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_cap_snooze_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "iteration_count >= 0",
            name="ck_triggers_iteration_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_triggers_event_id_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_triggers"),
    )
    op.create_index("ix_triggers_event_id", "triggers", ["event_id"], unique=False)
    op.create_index("ix_trigger_due", "triggers", ["state", "next_fire_at"], unique=False)

    # ─── nudge_logs ────────────────────────────────────────────────
    op.create_table(
        "nudge_logs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trigger_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "tone_used",
            sa.Enum(*_TONE_STEP, name="tone_step", native_enum=False, length=12),
            nullable=False,
        ),
        sa.Column(
            "voice_profile_used",
            sa.Enum(*_VOICE_PROFILE, name="voice_profile", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "user_reaction",
            sa.Enum(*_NUDGE_REACTION, name="nudge_reaction", native_enum=False, length=24),
            nullable=True,
        ),
        sa.Column("reaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["triggers.id"],
            name="fk_nudge_logs_trigger_id_triggers",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nudge_logs"),
    )
    op.create_index(
        "ix_nudge_log_trigger_fired", "nudge_logs", ["trigger_id", "fired_at"], unique=False
    )

    # ─── nudge_cache ───────────────────────────────────────────────
    # Deliberately has NO foreign key to users — see model docstring.
    op.create_table(
        "nudge_cache",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "response_meta",
            # Native JSONB on Postgres (prod target), JSON fallback on
            # SQLite so dev smoke-tests of the migration can run without
            # a Postgres container.
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "hit_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nudge_cache"),
        sa.UniqueConstraint("cache_key", name="uq_nudge_cache_key"),
    )
    op.create_index("ix_nudge_cache_cache_key", "nudge_cache", ["cache_key"], unique=False)

    # ─── voice_diary ───────────────────────────────────────────────
    op.create_table(
        "voice_diary",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("audio_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("transcript_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("duration_sec", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_voice_diary_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_voice_diary"),
    )
    op.create_index(
        "ix_diary_owner_created", "voice_diary", ["owner_user_id", "created_at"], unique=False
    )


def downgrade() -> None:
    # Drop in reverse dependency order. FKs are CASCADE on delete so the
    # data is gone too — downgrade from 0001 wipes the entire schema.
    op.drop_index("ix_diary_owner_created", table_name="voice_diary")
    op.drop_table("voice_diary")

    op.drop_index("ix_nudge_cache_cache_key", table_name="nudge_cache")
    op.drop_table("nudge_cache")

    op.drop_index("ix_nudge_log_trigger_fired", table_name="nudge_logs")
    op.drop_table("nudge_logs")

    op.drop_index("ix_trigger_due", table_name="triggers")
    op.drop_index("ix_triggers_event_id", table_name="triggers")
    op.drop_table("triggers")

    op.drop_index("ix_events_owner_user_id", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_users_ical_token", table_name="users")
    op.drop_index("ix_users_tg_id", table_name="users")
    op.drop_table("users")
