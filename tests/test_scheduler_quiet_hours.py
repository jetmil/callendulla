# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.scheduler.quiet_hours`."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from callendulla.scheduler.quiet_hours import (
    JITTER_MAX_MIN,
    is_quiet_now,
    next_post_quiet,
)


def _local(tz: str, year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper to build a UTC datetime that corresponds to a given local time."""
    local = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz))
    return local.astimezone(UTC)


class TestIsQuietNow:
    # ── Wrap-midnight window: classic 22→9 ─────────────────────────
    @pytest.mark.parametrize(
        ("local_hour", "expected"),
        [
            (8, True),  # before 9
            (9, False),  # at 9 — already awake
            (12, False),
            (21, False),
            (22, True),  # at 22 — already quiet
            (23, True),
            (0, True),
            (3, True),
        ],
    )
    def test_wrap_midnight(self, local_hour: int, expected: bool) -> None:
        now = _local("Europe/Moscow", 2026, 6, 1, local_hour)
        assert (
            is_quiet_now(
                now_utc=now,
                timezone="Europe/Moscow",
                from_hour=22,
                to_hour=9,
            )
            == expected
        )

    # ── Same-day window: e.g. work hours 9→17 ───────────────────────
    @pytest.mark.parametrize(
        ("local_hour", "expected"),
        [
            (8, False),
            (9, True),
            (12, True),
            (16, True),
            (17, False),
            (22, False),
        ],
    )
    def test_same_day(self, local_hour: int, expected: bool) -> None:
        now = _local("Europe/Moscow", 2026, 6, 1, local_hour)
        assert (
            is_quiet_now(
                now_utc=now,
                timezone="Europe/Moscow",
                from_hour=9,
                to_hour=17,
            )
            == expected
        )

    def test_different_timezones_disagree(self) -> None:
        """Same UTC moment, different TZs → different verdicts."""
        # 23:00 UTC = 02:00 Europe/Moscow (quiet) / 17:00 PST-08 (not quiet)
        now = datetime(2026, 6, 1, 23, 0, tzinfo=UTC)
        assert is_quiet_now(now_utc=now, timezone="Europe/Moscow", from_hour=22, to_hour=9) is True
        assert (
            is_quiet_now(
                now_utc=now,
                timezone="America/Los_Angeles",
                from_hour=22,
                to_hour=9,
            )
            is False
        )


class TestNextPostQuiet:
    def test_returns_at_or_after_to_hour(self) -> None:
        # 02:00 Moscow, quiet 22..9 → next is 09:00+jitter today
        now = _local("Europe/Moscow", 2026, 6, 1, 2)
        rng = random.Random(42)
        target = next_post_quiet(
            now_utc=now,
            timezone="Europe/Moscow",
            from_hour=22,
            to_hour=9,
            rng=rng,
        )
        local_target = target.astimezone(ZoneInfo("Europe/Moscow"))
        assert local_target.hour == 9
        assert local_target.minute <= JITTER_MAX_MIN
        assert local_target.date() == datetime(2026, 6, 1).date()

    def test_evening_call_wraps_to_next_day(self) -> None:
        # 23:00 Moscow today → next 09:00+jitter is tomorrow
        now = _local("Europe/Moscow", 2026, 6, 1, 23)
        rng = random.Random(42)
        target = next_post_quiet(
            now_utc=now,
            timezone="Europe/Moscow",
            from_hour=22,
            to_hour=9,
            rng=rng,
        )
        local_target = target.astimezone(ZoneInfo("Europe/Moscow"))
        assert local_target.date() == datetime(2026, 6, 2).date()

    def test_jitter_bounded(self) -> None:
        """Repeated calls must stay inside [0, JITTER_MAX_MIN] minutes."""
        now = _local("Europe/Moscow", 2026, 6, 1, 2)
        for seed in range(20):
            target = next_post_quiet(
                now_utc=now,
                timezone="Europe/Moscow",
                from_hour=22,
                to_hour=9,
                rng=random.Random(seed),
            )
            local = target.astimezone(ZoneInfo("Europe/Moscow"))
            assert 0 <= local.minute <= JITTER_MAX_MIN

    def test_returns_utc(self) -> None:
        now = _local("Europe/Moscow", 2026, 6, 1, 2)
        target = next_post_quiet(
            now_utc=now,
            timezone="Europe/Moscow",
            from_hour=22,
            to_hour=9,
            rng=random.Random(0),
        )
        # ZoneInfo("UTC") and datetime.timezone.utc are distinct objects
        # but both report utcoffset() == 0 — compare via offset, not
        # identity.
        assert target.utcoffset() == timedelta(0)


class TestEdgeCases:
    def test_one_minute_before_to_hour(self) -> None:
        """At local 08:59 we are still quiet."""
        now = _local("Europe/Moscow", 2026, 6, 1, 8, 59)
        assert is_quiet_now(now_utc=now, timezone="Europe/Moscow", from_hour=22, to_hour=9) is True

    def test_one_minute_after_to_hour(self) -> None:
        now = _local("Europe/Moscow", 2026, 6, 1, 9, 1)
        assert is_quiet_now(now_utc=now, timezone="Europe/Moscow", from_hour=22, to_hour=9) is False

    def test_at_from_hour_boundary(self) -> None:
        """At local 22:00 sharp we are already quiet (inclusive lower bound)."""
        now = _local("Europe/Moscow", 2026, 6, 1, 22, 0)
        assert is_quiet_now(now_utc=now, timezone="Europe/Moscow", from_hour=22, to_hour=9) is True


# Avoid 'unused import' lint when timedelta is not used directly.
_ = timedelta
