# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""TTS provider Protocol + error type."""

from __future__ import annotations

from typing import Protocol


class TTSError(Exception):
    """Single error type for synthesis failures.

    Net, auth, voice-not-found, format-mismatch — all surface here.
    Caller decides whether to retry, fall back to text-only, or
    surface to the user.
    """


class TTSProvider(Protocol):
    """Synthesise a short text into voice-message audio bytes."""

    async def synthesize(
        self,
        text: str,
        *,
        voice: str | None = None,
    ) -> bytes:
        """Return OGG/Opus bytes suitable for :py:meth:`Bot.send_voice`.

        ``voice`` is an engine-specific identifier (e.g. edge-tts uses
        ``"ru-RU-SvetlanaNeural"``). ``None`` means engine-default.

        Implementations MUST raise :class:`TTSError` on any failure;
        upstream exceptions may carry connection details and should
        not leak.
        """
        ...
