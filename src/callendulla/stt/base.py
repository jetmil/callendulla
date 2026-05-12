# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""STT provider Protocol + shared error type."""

from __future__ import annotations

from typing import Protocol


class STTError(Exception):
    """Single error type for transcription failures.

    Net, auth, rate-limit, format — all surface as :class:`STTError`.
    The diary background task catches it, logs through ``safe_repr``,
    and leaves ``transcript_ciphertext`` as the empty placeholder.
    """


class STTProvider(Protocol):
    """Transcribe an audio blob to text."""

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        fmt: str = "ogg",
        language: str | None = None,
    ) -> str:
        """Return the recognised transcript.

        ``fmt`` is the input file extension hint (``ogg``, ``mp3``,
        ``wav``). ``language`` is an ISO-639-1 code or ``None`` for
        autodetect.

        Implementations MUST raise :class:`STTError` for any failure —
        no upstream exception leaking (its ``repr`` may carry the API
        key).
        """
        ...
