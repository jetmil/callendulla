# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""SQLAlchemy declarative models for callendulla.

Schema overview::

    User (1) ──< (N) Event
                    │
                    └──< (N) Trigger ──< (N) NudgeLog

    User (1) ──< (N) VoiceDiary

    NudgeCache  — keyed by a hash, no FK to User: deliberately
                  unidentifiable across tenants so a LLM-response
                  payload can never be associated with a specific user
                  by anyone reading the table.

Notes on design choices:

- All timestamps are ``TIMESTAMP WITH TIME ZONE``. Per-user local times
  for quiet hours / display are derived by converting on read.
- ``Event.title`` and ``Event.description`` stay plaintext — they are
  what the LLM sees to compose nudges. Sensitive content goes through
  ``VoiceDiary`` which is Fernet-encrypted blob-only.
- Tone state lives on ``Trigger`` (not on ``Event``) — one event can
  have multiple triggers (multiple reminders before the due time), and
  each escalates independently.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callendulla.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    pass

# ─────────────────────────────────────────────────────────────────
# Enums (StrEnum so values land in DB as readable strings, not ints).
# ─────────────────────────────────────────────────────────────────


class UserRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class UserStatus(StrEnum):
    ACTIVE = "active"
    PENDING_INVITE = "pending_invite"
    DISABLED = "disabled"


class VoiceProfile(StrEnum):
    """Six prerecorded archetypes. Real WIP — full bank lands later."""

    BRUTAL_BRO = "brutal_bro"
    WARM_SISTER = "warm_sister"
    OFFICE_NEUTRAL = "office_neutral"
    DRILL_SERGEANT = "drill_sergeant"
    IRON_LADY = "iron_lady"
    QUIET_MENTOR = "quiet_mentor"


class TriggerKind(StrEnum):
    """How the trigger schedules itself."""

    ONESHOT = "oneshot"  # fire at one absolute moment
    INTERVAL = "interval"  # every N seconds from anchor
    CRON = "cron"  # cron-spec expression


class TriggerState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    SNOOZED = "snoozed"
    DONE = "done"
    DISABLED = "disabled"


class ToneStep(StrEnum):
    """Four steps inside any voice profile, from soft to hard."""

    SOFT = "soft"
    NORMAL = "normal"
    SHARP = "sharp"
    HARD = "hard"


class NudgeReaction(StrEnum):
    """What user did with a fired nudge. ``NULL`` = no reaction yet."""

    ACK = "ack"  # ✅ сделал
    SNOOZE_1H = "snooze_1h"  # 💤
    SNOOZE_TOMORROW = "snooze_tomorrow"  # 🌅
    SILENT_12H = "silent_12h"  # 🔇


# ─────────────────────────────────────────────────────────────────
# User
# ─────────────────────────────────────────────────────────────────


class User(TimestampMixin, Base):
    """A Telegram identity that the bot serves.

    ``tg_id`` is the canonical id. ``tg_username`` may change at any time
    and is kept only for display / whitelist lookups, never for auth.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "quiet_from_hour BETWEEN 0 AND 23 AND quiet_to_hour BETWEEN 0 AND 23 "
            "AND quiet_from_hour <> quiet_to_hour",
            name="quiet_hours_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, index=True)
    tg_username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(120))

    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=16),
        nullable=False,
        default=UserRole.MEMBER,
    )
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status", native_enum=False, length=24),
        nullable=False,
        default=UserStatus.ACTIVE,
    )

    voice_profile: Mapped[VoiceProfile] = mapped_column(
        SAEnum(VoiceProfile, name="voice_profile", native_enum=False, length=32),
        nullable=False,
        default=VoiceProfile.WARM_SISTER,
    )

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")
    quiet_from_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    quiet_to_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=9)

    # Opaque per-user token for the iCal feed URL. Random at creation,
    # rotatable via /rotate_ical. NEVER reveal in logs.
    ical_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    events: Mapped[list[Event]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    diary_entries: Mapped[list[VoiceDiary]] = relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        # ical_token deliberately omitted; tg_id is not secret but is PII —
        # keep it out of casual repr() that may land in logs.
        return f"<User id={self.id} role={self.role.value}>"


# ─────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────


class Event(TimestampMixin, Base):
    """A thing the user wants to be reminded of.

    The reminder cadence itself lives in child :class:`Trigger` rows —
    an event can have many triggers (e.g. -1 day, -1 hour, at start).
    Recurrence is RFC 5545 rrule encoded as a string on the event.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    dtstart: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dtend: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")

    # RFC 5545 rrule string, e.g. "FREQ=WEEKLY;BYDAY=MO,WE,FR". NULL = one-shot.
    rrule: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    owner: Mapped[User] = relationship(back_populates="events", lazy="joined")
    triggers: Mapped[list[Trigger]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Event id={self.id} owner={self.owner_user_id} dtstart={self.dtstart.isoformat()}>"


# ─────────────────────────────────────────────────────────────────
# Trigger
# ─────────────────────────────────────────────────────────────────


class Trigger(TimestampMixin, Base):
    """One scheduled nudge cadence attached to an event.

    Persistent state for the escalation loop lives here:
    ``current_tone`` + ``iteration_count`` + ``last_fired_at``. APScheduler
    re-reads it every tick; the engine never holds it in memory.
    """

    __tablename__ = "triggers"
    __table_args__ = (
        CheckConstraint(
            "iteration_count >= 0",
            name="iteration_non_negative",
        ),
        Index("ix_trigger_due", "state", "next_fire_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    kind: Mapped[TriggerKind] = mapped_column(
        SAEnum(TriggerKind, name="trigger_kind", native_enum=False, length=16),
        nullable=False,
    )
    state: Mapped[TriggerState] = mapped_column(
        SAEnum(TriggerState, name="trigger_state", native_enum=False, length=16),
        nullable=False,
        default=TriggerState.PENDING,
    )

    # Free-form schedule descriptor, semantics depend on ``kind``:
    #   oneshot  → ISO 8601 datetime
    #   interval → "<seconds>:<anchor-iso>"
    #   cron     → cron 5-field string
    schedule_spec: Mapped[str] = mapped_column(String(256), nullable=False)

    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    current_tone: Mapped[ToneStep] = mapped_column(
        SAEnum(ToneStep, name="tone_step", native_enum=False, length=12),
        nullable=False,
        default=ToneStep.SOFT,
    )
    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Cap-escalation guard: when the tone hits HARD and N iterations pass
    # with no reaction, the engine snoozes the trigger for 12 h. This
    # column records the last time that guard fired so we don't loop on
    # the snooze itself.
    last_cap_snooze_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="triggers", lazy="joined")
    nudges: Mapped[list[NudgeLog]] = relationship(
        back_populates="trigger",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<Trigger id={self.id} event={self.event_id} "
            f"state={self.state.value} tone={self.current_tone.value} "
            f"iter={self.iteration_count}>"
        )


# ─────────────────────────────────────────────────────────────────
# NudgeLog
# ─────────────────────────────────────────────────────────────────


class NudgeLog(Base):
    """Append-only record of every fired nudge and the user's reaction.

    No ``TimestampMixin``: the only relevant time is ``fired_at`` which
    is set explicitly when the row is created.
    """

    __tablename__ = "nudge_logs"
    __table_args__ = (Index("ix_nudge_log_trigger_fired", "trigger_id", "fired_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trigger_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("triggers.id", ondelete="CASCADE"),
        nullable=False,
    )

    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    tone_used: Mapped[ToneStep] = mapped_column(
        SAEnum(ToneStep, name="tone_step", native_enum=False, length=12),
        nullable=False,
    )
    voice_profile_used: Mapped[VoiceProfile] = mapped_column(
        SAEnum(VoiceProfile, name="voice_profile", native_enum=False, length=32),
        nullable=False,
    )

    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)

    user_reaction: Mapped[NudgeReaction | None] = mapped_column(
        SAEnum(NudgeReaction, name="nudge_reaction", native_enum=False, length=24)
    )
    reaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trigger: Mapped[Trigger] = relationship(back_populates="nudges", lazy="joined")

    def __repr__(self) -> str:
        return (
            f"<NudgeLog id={self.id} trigger={self.trigger_id} "
            f"tone={self.tone_used.value} reaction={self.user_reaction}>"
        )


# ─────────────────────────────────────────────────────────────────
# NudgeCache
# ─────────────────────────────────────────────────────────────────


class NudgeCache(Base):
    """LLM-response cache keyed by a content-hash.

    Deliberately has **no** FK to ``users`` — the cache key is a SHA-256
    of (profile, tone, anonymised-prompt-shape). This makes cross-user
    sharing safe: two users with the same kind of late-running event get
    the same cached response, without anything in the table tying either
    user to it.

    Plain user data (event titles, names, free text) MUST be replaced
    with stable placeholders before hashing — see
    :func:`callendulla.scheduler.cache.compose_key`. A test verifies the
    placeholder discipline.
    """

    __tablename__ = "nudge_cache"
    __table_args__ = (UniqueConstraint("cache_key", name="uq_nudge_cache_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_meta: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<NudgeCache id={self.id} key={self.cache_key[:8]}…>"


# ─────────────────────────────────────────────────────────────────
# VoiceDiary
# ─────────────────────────────────────────────────────────────────


class VoiceDiary(Base):
    """One voice-diary entry, stored as Fernet ciphertext at rest.

    The plain text never lands on disk — recording arrives via Telegram,
    the bot transcribes it, encrypts with ``DIARY_ENCRYPTION_KEY``, and
    persists only the ciphertext. Decryption happens transiently in
    process memory when the owner asks to read.
    """

    __tablename__ = "voice_diary"
    __table_args__ = (Index("ix_diary_owner_created", "owner_user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    audio_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    transcript_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    duration_sec: Mapped[float | None] = mapped_column()  # may be NULL on import
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    owner: Mapped[User] = relationship(back_populates="diary_entries", lazy="joined")

    def __repr__(self) -> str:
        return f"<VoiceDiary id={self.id} owner={self.owner_user_id}>"


# Public re-exports — what other modules import.
__all__ = [
    "Event",
    "NudgeCache",
    "NudgeLog",
    "NudgeReaction",
    "ToneStep",
    "Trigger",
    "TriggerKind",
    "TriggerState",
    "User",
    "UserRole",
    "UserStatus",
    "VoiceDiary",
    "VoiceProfile",
]
