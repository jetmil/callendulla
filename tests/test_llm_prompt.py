# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.llm.prompt`.

Cross-user safety here is the most important invariant — the prompt
composer must not accept other users' data, and the rendered prompt
must contain the title we passed in (no swap with another buffer).
"""

from __future__ import annotations

import pytest

from callendulla.db.models import ToneStep, VoiceProfile
from callendulla.llm.prompt import compose_nudge_prompt


class TestComposeNudgePrompt:
    def test_includes_title_verbatim(self) -> None:
        prompt = compose_nudge_prompt(
            profile=VoiceProfile.WARM_SISTER,
            tone=ToneStep.SOFT,
            title="забрать посылку",
        )
        assert "забрать посылку" in prompt

    @pytest.mark.parametrize("profile", list(VoiceProfile))
    @pytest.mark.parametrize("tone", list(ToneStep))
    def test_every_combination_renders(self, profile: VoiceProfile, tone: ToneStep) -> None:
        prompt = compose_nudge_prompt(profile=profile, tone=tone, title="X")
        assert prompt
        assert "X" in prompt

    def test_persona_differs_per_profile(self) -> None:
        a = compose_nudge_prompt(profile=VoiceProfile.BRUTAL_BRO, tone=ToneStep.SOFT, title="X")
        b = compose_nudge_prompt(profile=VoiceProfile.QUIET_MENTOR, tone=ToneStep.SOFT, title="X")
        assert a != b

    def test_directive_differs_per_tone(self) -> None:
        a = compose_nudge_prompt(profile=VoiceProfile.WARM_SISTER, tone=ToneStep.SOFT, title="X")
        b = compose_nudge_prompt(profile=VoiceProfile.WARM_SISTER, tone=ToneStep.HARD, title="X")
        assert a != b

    def test_no_keyword_only_arg_leakage(self) -> None:
        """All three args are keyword-only — call without them must fail."""
        with pytest.raises(TypeError):
            compose_nudge_prompt("X", ToneStep.SOFT, "title")  # type: ignore[misc]
