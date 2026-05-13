# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Pick an :class:`TTSProvider` implementation from :class:`Settings`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from callendulla.config import TTSEngine

if TYPE_CHECKING:
    from callendulla.config import Settings
    from callendulla.tts.base import TTSProvider


def build_tts_provider(settings: Settings) -> TTSProvider | None:
    """Return the configured provider or ``None`` if TTS is unavailable.

    ``None`` is a first-class state: the bot operates without voice
    output, calendar reminders go as text only.
    """
    if settings.tts_engine is TTSEngine.EDGE:
        from callendulla.tts.edge import EdgeTTSProvider  # noqa: PLC0415

        return EdgeTTSProvider()

    # Piper + CosyVoice paths land in follow-up PRs. For now,
    # everything that isn't ``edge`` falls back to "no TTS" — bot
    # works in text-only mode without complaints.
    return None
