# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Inline keyboards used by callendulla.

The nudge keyboard is the only one for now — four buttons under every
nudge message: ack / snooze 1h / postpone to tomorrow / silent 12h.

Callback data shape:
    ``nudge:<nudge_log_id>:<action>``

Why a tiny custom encoding instead of aiogram's CallbackData class:
the payload must stay under 64 bytes (Telegram limit) and decoding
needs to be readable from a unit test without instantiating aiogram
internals. The colon separator is safe — actions are a fixed enum.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class NudgeAction(StrEnum):
    """User reactions exposed on the nudge keyboard."""

    ACK = "ack"
    SNOOZE_1H = "snooze_1h"
    SNOOZE_TOMORROW = "snooze_tomorrow"
    SILENT_12H = "silent_12h"


CALLBACK_PREFIX: Final[str] = "nudge"


def build_callback_data(nudge_log_id: int, action: NudgeAction) -> str:
    """Encode ``(nudge_log_id, action)`` into Telegram callback_data.

    Telegram caps callback_data at 64 bytes — our format leaves
    plenty of headroom: ``"nudge:" + 12-digit id + ":" + 16-char action``.
    """
    payload = f"{CALLBACK_PREFIX}:{nudge_log_id}:{action.value}"
    if len(payload.encode("utf-8")) > 64:  # pragma: no cover — defensive
        msg = f"callback payload too long: {payload!r}"
        raise ValueError(msg)
    return payload


def parse_callback_data(raw: str) -> tuple[int, NudgeAction] | None:
    """Reverse of :func:`build_callback_data`.

    Returns ``None`` for malformed input — handler treats malformed
    callbacks as "not ours, ignore" rather than 500-ing.
    """
    parts = raw.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    try:
        nudge_log_id = int(parts[1])
    except ValueError:
        return None
    try:
        action = NudgeAction(parts[2])
    except ValueError:
        return None
    return nudge_log_id, action


def nudge_keyboard(nudge_log_id: int) -> InlineKeyboardMarkup:
    """Two-row keyboard with the four reaction buttons."""
    row1 = [
        InlineKeyboardButton(
            text="✅ сделал",
            callback_data=build_callback_data(nudge_log_id, NudgeAction.ACK),
        ),
        InlineKeyboardButton(
            text="💤 +1ч",
            callback_data=build_callback_data(nudge_log_id, NudgeAction.SNOOZE_1H),
        ),
    ]
    row2 = [
        InlineKeyboardButton(
            text="🌅 завтра",
            callback_data=build_callback_data(nudge_log_id, NudgeAction.SNOOZE_TOMORROW),
        ),
        InlineKeyboardButton(
            text="🔇 12ч тихо",
            callback_data=build_callback_data(nudge_log_id, NudgeAction.SILENT_12H),
        ),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[row1, row2])
