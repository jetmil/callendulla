# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Callback-query handler for the nudge reaction buttons.

Flow:

1. Telegram POSTs a ``callback_query`` when the user taps a button.
2. UserMiddleware resolves the caller to a User row (or None).
3. This handler parses the payload, looks up the NudgeLog, verifies
   the *caller is its owner*, and applies the action:

   - ``ack``               → Trigger.state = DONE, no further fires
   - ``snooze_1h``         → next_fire_at = now + 1h
   - ``snooze_tomorrow``   → next_fire_at = 09:00 local-time + jitter
   - ``silent_12h``        → state = SNOOZED, next_fire_at = now + 12h

4. NudgeLog.user_reaction + reaction_at are written either way —
   this is what the cap-escalation guard in nudge_engine reads.

Cross-user safety: the same NudgeLog row owner check is the hinge.
Anyone can guess a nudge_log_id, but only the owning user can
modify their own nudge.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from aiogram import Router, types
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from callendulla.bot.keyboards import CALLBACK_PREFIX, NudgeAction, parse_callback_data
from callendulla.db.models import (
    Event,
    NudgeLog,
    NudgeReaction,
    Trigger,
    TriggerState,
)
from callendulla.scheduler.quiet_hours import next_post_quiet

if TYPE_CHECKING:
    from callendulla.db.models import User
    from callendulla.db.session import SessionFactory

router = Router(name="reactions")


def _now() -> datetime:
    return datetime.now(tz=UTC)


@router.callback_query(lambda cq: cq.data and cq.data.startswith(CALLBACK_PREFIX + ":"))
async def handle_nudge_reaction(
    callback_query: types.CallbackQuery,
    user: User | None,
    session_factory: SessionFactory,
) -> None:
    if user is None:
        await callback_query.answer("Сначала /start", show_alert=False)
        return

    parsed = parse_callback_data(callback_query.data or "")
    if parsed is None:
        await callback_query.answer()  # silently dismiss malformed
        return
    nudge_log_id, action = parsed

    async with session_factory() as session:
        # Load NudgeLog with its trigger + event + owner so we can do
        # the owner check and the state update in one transaction.
        stmt = (
            select(NudgeLog)
            .where(NudgeLog.id == nudge_log_id)
            .options(
                selectinload(NudgeLog.trigger).selectinload(Trigger.event).selectinload(Event.owner)
            )
        )
        log = (await session.execute(stmt)).scalar_one_or_none()

        if log is None or log.trigger.event.owner_user_id != user.id:
            # Same response for "not yours" and "doesn't exist" — never
            # leak the existence of another user's nudge ID.
            await callback_query.answer("Эта кнопка не для тебя.", show_alert=False)
            return

        # Idempotency: if reaction already set, just acknowledge.
        if log.user_reaction is not None:
            await callback_query.answer(_ack_text_for(action), show_alert=False)
            return

        now = _now()
        log.user_reaction = _reaction_for(action)
        log.reaction_at = now

        trigger = log.trigger
        owner = log.trigger.event.owner
        _apply_action_to_trigger(trigger, owner, action, now)

        await session.commit()

    await callback_query.answer(_ack_text_for(action), show_alert=False)
    logger.info(
        "nudge {nudge_id}: user {user_id} reacted {action}",
        nudge_id=nudge_log_id,
        user_id=user.id,
        action=action.value,
    )


def _reaction_for(action: NudgeAction) -> NudgeReaction:
    """Map button to ``NudgeReaction`` enum value.

    ``NudgeReaction`` and ``NudgeAction`` share the same string values
    by construction — but they're distinct enum classes, so we convert
    via ``.value``. If the strings ever diverge, this is the one place
    that breaks loudly instead of silently writing wrong data.
    """
    return NudgeReaction(action.value)


def _ack_text_for(action: NudgeAction) -> str:
    return {
        NudgeAction.ACK: "Принято ✅",
        NudgeAction.SNOOZE_1H: "Напомню через час 💤",
        NudgeAction.SNOOZE_TOMORROW: "До завтра 🌅",
        NudgeAction.SILENT_12H: "Молчу 12 часов 🔇",
    }[action]


def _apply_action_to_trigger(
    trigger: Trigger, owner: User, action: NudgeAction, now: datetime
) -> None:
    """Mutate the trigger to reflect the user's choice.

    Pure function over (trigger, owner, action, now). No session
    interaction — the caller commits.
    """
    if action is NudgeAction.ACK:
        # ack → done; do not fire again
        trigger.state = TriggerState.DONE
        trigger.next_fire_at = None
        return

    if action is NudgeAction.SNOOZE_1H:
        trigger.state = TriggerState.PENDING
        trigger.next_fire_at = now + timedelta(hours=1)
        return

    if action is NudgeAction.SNOOZE_TOMORROW:
        trigger.state = TriggerState.PENDING
        # Snap to next 09:00 local-time + jitter via the same helper
        # quiet hours uses, so behaviour is consistent.
        trigger.next_fire_at = next_post_quiet(
            now_utc=now,
            timezone=owner.timezone,
            from_hour=owner.quiet_from_hour,
            to_hour=owner.quiet_to_hour,
            rng=random.SystemRandom(),
        )
        return

    if action is NudgeAction.SILENT_12H:
        trigger.state = TriggerState.SNOOZED
        trigger.next_fire_at = now + timedelta(hours=12)
        return
