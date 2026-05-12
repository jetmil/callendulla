# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Per-user quiet hours: don't fire reminders during sleep window.

Quiet window is ``[from_hour, to_hour)`` in the user's *local* time.
Window can wrap midnight: ``from=22, to=9`` means "22:00 through
08:59:59 inclusive is quiet".

The scheduler invokes :func:`is_quiet_now` before firing a trigger.
On hit, it defers the trigger to the next ``to_hour`` plus a small
jitter (so 50 users with quiet 9→22 don't all stampede at exactly
09:00).

Why this lives outside ``nudge_engine.py``: it has no dependencies on
the rest of the engine and is the most-tested branch of the firing
decision. Extracting it keeps both files focused.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

JITTER_MAX_MIN: Final[int] = 30
"""Max jitter added after quiet window ends, in minutes."""


def is_quiet_now(
    *,
    now_utc: datetime,
    timezone: str,
    from_hour: int,
    to_hour: int,
) -> bool:
    """``True`` iff the user's *local* current hour falls in the quiet window.

    ``from_hour == to_hour`` is caught by ``Settings`` validation, so we
    don't need to handle "always quiet" / "never quiet" here.
    """
    local = now_utc.astimezone(ZoneInfo(timezone))
    hour = local.hour
    if from_hour < to_hour:
        # Same-day window: e.g. 9..17 = work hours.
        return from_hour <= hour < to_hour
    # Wraps midnight: e.g. 22..9 = night.
    return hour >= from_hour or hour < to_hour


def next_post_quiet(
    *,
    now_utc: datetime,
    timezone: str,
    from_hour: int,
    to_hour: int,
    rng: random.Random | None = None,
) -> datetime:
    """Return the next moment at or after the quiet window ends.

    Adds 0..30 minutes of uniform jitter to prevent multiple deferred
    triggers stampeding the moment quiet hours end. Returns UTC.
    """
    chooser = rng if rng is not None else random.SystemRandom()
    tz = ZoneInfo(timezone)
    local = now_utc.astimezone(tz)
    # Build target local datetime at to_hour:00 today.
    target = local.replace(hour=to_hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    jitter = timedelta(minutes=chooser.randint(0, JITTER_MAX_MIN))
    return (target + jitter).astimezone(ZoneInfo("UTC"))
