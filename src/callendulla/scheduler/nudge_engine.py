# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Core firing loop: due triggers → message + state update.

Lessons baked in (private dev predecessor learned them the hard way):

1. ``misfire_grace_time`` defaults to 1 second in APScheduler —
   anything blocking the event loop briefly drops scheduled jobs
   silently. We pass ``misfire_grace_time=60`` from :mod:`main`.
2. **Cap escalation**: once tone reaches HARD and the user has
   ignored 3 consecutive nudges (``user_reaction IS NULL`` on the
   last N logs), snooze the trigger for 12 h and reset tone to SOFT.
   Without this, an ignored event sprays escalating mat-language at
   the user every 10 minutes forever.
3. **Quiet hours**: per-user local-time window. Deferred triggers
   get bumped to the next post-quiet moment plus jitter so 50 users
   with the same window don't stampede at 09:00.

The flow per due trigger:
  load → quiet check → cap check → render text → send → update state.
LLM-driven text generation slots into :func:`_compose_message` without
changing the rest of the loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from callendulla.db.models import (
    Event,
    NudgeLog,
    ToneStep,
    Trigger,
    TriggerKind,
    TriggerState,
    User,
)
from callendulla.llm.base import LLMError, LLMProvider
from callendulla.llm.prompt import compose_nudge_prompt
from callendulla.scheduler.quiet_hours import is_quiet_now, next_post_quiet
from callendulla.scheduler.tones import (
    CAP_ITERATIONS_WITHOUT_REACTION,
    CAP_SNOOZE,
    CAP_TONE,
    escalate,
    interval_after,
    render_nudge,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callendulla.db.session import SessionFactory


class _MessageSender(Protocol):
    """The shape we need out of an aiogram :class:`Bot` (or test stub).

    Only ``send_message`` is exercised here — keeping the contract this
    narrow lets the test stub stay 5 lines.
    """

    async def send_message(self, *, chat_id: int, text: str, **kwargs: object) -> object: ...


class NudgeEngine:
    """Stateful container so ``run_once`` can be invoked from a job loop."""

    def __init__(
        self,
        session_factory: SessionFactory,
        bot: _MessageSender,
        llm: LLMProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._bot = bot
        # When ``llm is None`` the engine uses the static template bank
        # for every nudge. Operators who don't set LLM_API_KEY or pick
        # an unreachable Ollama still get something usable.
        self._llm = llm

    async def run_once(self, *, now_utc: datetime | None = None) -> int:
        """Process every trigger whose ``next_fire_at`` is due.

        Returns the number of triggers it touched (fired, deferred, or
        capped) — useful for tests and for the scheduler's tick log.
        """
        now = now_utc or datetime.now(tz=UTC)
        touched = 0
        async with self._session_factory() as session:
            due_triggers = await self._load_due(session, now=now)
            for trigger in due_triggers:
                await self._process_one(session, trigger, now=now)
                touched += 1
        return touched

    async def _load_due(self, session: AsyncSession, *, now: datetime) -> list[Trigger]:
        stmt = (
            select(Trigger)
            .where(
                Trigger.state == TriggerState.PENDING,
                Trigger.next_fire_at.isnot(None),
                Trigger.next_fire_at <= now,
            )
            .options(selectinload(Trigger.event).selectinload(Event.owner))
            .order_by(Trigger.next_fire_at.asc())
        )
        return list((await session.execute(stmt)).scalars())

    async def _process_one(
        self,
        session: AsyncSession,
        trigger: Trigger,
        *,
        now: datetime,
    ) -> None:
        event = trigger.event
        user = event.owner
        if not event.is_active:
            trigger.state = TriggerState.DONE
            await session.commit()
            return

        # ── 1) Quiet hours guard ─────────────────────────────────
        if is_quiet_now(
            now_utc=now,
            timezone=user.timezone,
            from_hour=user.quiet_from_hour,
            to_hour=user.quiet_to_hour,
        ):
            new_fire = next_post_quiet(
                now_utc=now,
                timezone=user.timezone,
                from_hour=user.quiet_from_hour,
                to_hour=user.quiet_to_hour,
            )
            logger.info(
                "trigger {trigger_id}: quiet hours, deferring to {new_fire}",
                trigger_id=trigger.id,
                new_fire=new_fire.isoformat(),
            )
            trigger.next_fire_at = new_fire
            await session.commit()
            return

        # ── 2) Cap-escalation guard ──────────────────────────────
        if trigger.current_tone is CAP_TONE:
            silent_count = await self._consecutive_silent_count(session, trigger)
            if silent_count >= CAP_ITERATIONS_WITHOUT_REACTION:
                logger.warning(
                    "trigger {trigger_id}: cap reached + {n} silent nudges → "
                    "12h snooze, tone reset",
                    trigger_id=trigger.id,
                    n=silent_count,
                )
                trigger.state = TriggerState.SNOOZED
                trigger.next_fire_at = now + CAP_SNOOZE
                trigger.last_cap_snooze_at = now
                trigger.current_tone = self._reset_tone()
                trigger.iteration_count = 0
                await session.commit()
                return

        # ── 3) Compose and send ──────────────────────────────────
        text = await self._compose_message(user, event, trigger)
        tg_message_id: int | None = None
        try:
            sent = await self._bot.send_message(chat_id=user.tg_id, text=text)
            tg_message_id = getattr(sent, "message_id", None)
        except Exception:
            # Best-effort: log + record. Don't blow up the loop, the
            # next tick will retry or escalate as usual.
            logger.exception(
                "trigger {trigger_id}: send_message failed, logging anyway",
                trigger_id=trigger.id,
            )

        log = NudgeLog(
            trigger_id=trigger.id,
            fired_at=now,
            tone_used=trigger.current_tone,
            voice_profile_used=user.voice_profile,
            message_text=text,
            tg_message_id=tg_message_id,
        )
        session.add(log)

        # ── 4) Schedule the next fire ────────────────────────────
        trigger.iteration_count += 1
        trigger.last_fired_at = now
        next_tone = escalate(trigger.current_tone)
        trigger.current_tone = next_tone
        # For a one-shot event we keep firing until the cap-guard
        # snoozes the trigger; recurring rrule support lands later.
        if trigger.kind is TriggerKind.ONESHOT:
            trigger.next_fire_at = now + interval_after(next_tone)
        await session.commit()

    @staticmethod
    async def _consecutive_silent_count(session: AsyncSession, trigger: Trigger) -> int:
        """How many of the most-recent NudgeLogs have NULL ``user_reaction``.

        Used by the cap guard. We don't need an exact count, just to
        compare against :data:`CAP_ITERATIONS_WITHOUT_REACTION`.
        """
        stmt = (
            select(NudgeLog)
            .where(NudgeLog.trigger_id == trigger.id)
            .order_by(desc(NudgeLog.fired_at))
            .limit(CAP_ITERATIONS_WITHOUT_REACTION)
        )
        logs = list((await session.execute(stmt)).scalars())
        if len(logs) < CAP_ITERATIONS_WITHOUT_REACTION:
            return len(logs) if all(log.user_reaction is None for log in logs) else 0
        return sum(1 for log in logs if log.user_reaction is None)

    async def _compose_message(self, user: User, event: Event, trigger: Trigger) -> str:
        """Render the nudge text. LLM-first with template fallback.

        If no LLM is wired in (``llm=None``) or any LLMError surfaces,
        we fall back to the static template bank — the user gets a
        message regardless of upstream availability. Cross-user safety:
        the prompt never includes other users' data — see
        :func:`compose_nudge_prompt`.
        """
        if self._llm is not None:
            prompt = compose_nudge_prompt(
                profile=user.voice_profile,
                tone=trigger.current_tone,
                title=event.title,
            )
            try:
                return await self._llm.generate(prompt)
            except LLMError as exc:
                logger.warning(
                    "trigger {trigger_id}: LLM failed, falling back to template ({reason})",
                    trigger_id=trigger.id,
                    reason=str(exc),
                )

        return render_nudge(
            profile=user.voice_profile,
            tone=trigger.current_tone,
            title=event.title,
        )

    @staticmethod
    def _reset_tone() -> ToneStep:
        return ToneStep.SOFT
