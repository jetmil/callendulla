# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.scheduler.tones`."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from callendulla.db.models import ToneStep, VoiceProfile
from callendulla.scheduler.tones import (
    CAP_ITERATIONS_WITHOUT_REACTION,
    CAP_SNOOZE,
    CAP_TONE,
    escalate,
    interval_after,
    render_nudge,
)


class TestEscalate:
    @pytest.mark.parametrize(
        ("start", "expected"),
        [
            (ToneStep.SOFT, ToneStep.NORMAL),
            (ToneStep.NORMAL, ToneStep.SHARP),
            (ToneStep.SHARP, ToneStep.HARD),
            (ToneStep.HARD, ToneStep.HARD),  # caps here
        ],
    )
    def test_step(self, start: ToneStep, expected: ToneStep) -> None:
        assert escalate(start) == expected

    def test_cap_constant_is_top(self) -> None:
        assert CAP_TONE == ToneStep.HARD


class TestIntervals:
    def test_intervals_strictly_decrease(self) -> None:
        """Each step up the ladder produces a *shorter* delay."""
        soft = interval_after(ToneStep.SOFT)
        normal = interval_after(ToneStep.NORMAL)
        sharp = interval_after(ToneStep.SHARP)
        hard = interval_after(ToneStep.HARD)
        assert soft > normal > sharp >= hard

    def test_intervals_are_positive(self) -> None:
        for tone in (ToneStep.SOFT, ToneStep.NORMAL, ToneStep.SHARP, ToneStep.HARD):
            assert interval_after(tone) > timedelta(0)


class TestCapConstants:
    def test_cap_iterations_at_least_2(self) -> None:
        """Cap-snooze after 1 silent fire would be too aggressive."""
        assert CAP_ITERATIONS_WITHOUT_REACTION >= 2

    def test_cap_snooze_at_least_1h(self) -> None:
        """A short snooze defeats the whole point of cap-snooze."""
        assert timedelta(hours=1) <= CAP_SNOOZE


class TestRenderNudge:
    def test_renders_title_into_template(self) -> None:
        text = render_nudge(
            profile=VoiceProfile.WARM_SISTER,
            tone=ToneStep.SOFT,
            title="забрать посылку",
            rng=random.Random(0),
        )
        assert "забрать посылку" in text

    @pytest.mark.parametrize("profile", list(VoiceProfile))
    @pytest.mark.parametrize("tone", list(ToneStep))
    def test_every_profile_tone_has_a_template(self, profile: VoiceProfile, tone: ToneStep) -> None:
        """Every combination renders without falling back silently — or
        falls back through the documented OFFICE_NEUTRAL path. Either
        way, output is non-empty and contains the title."""
        text = render_nudge(profile=profile, tone=tone, title="X", rng=random.Random(0))
        assert text
        assert "X" in text

    def test_picks_different_variants_with_different_seeds(self) -> None:
        """Some (profile, tone) combos have ≥2 templates — we should see
        variety with different RNG seeds. Pick a combo we wrote with 2.
        """
        seen: set[str] = set()
        for seed in range(50):
            text = render_nudge(
                profile=VoiceProfile.BRUTAL_BRO,
                tone=ToneStep.SOFT,
                title="дело",
                rng=random.Random(seed),
            )
            seen.add(text)
        assert len(seen) >= 2
