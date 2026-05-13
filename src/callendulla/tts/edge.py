# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""edge-tts adapter — Microsoft Azure free TTS endpoint, no API key.

Bypasses any LLM provider. Useful for the audio-out of nudges and for
re-reading diary transcripts. Outbound HTTPS to Azure required.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.tts.base import TTSError

_DEFAULT_VOICE: str = "ru-RU-SvetlanaNeural"


class EdgeTTSProvider:
    """Adapter over :mod:`edge_tts`.

    The SDK is imported lazily so importing this module doesn't pull
    edge-tts into the graph of callers that just want
    :class:`TTSProvider` for typing.
    """

    def __init__(self, *, voice: str | None = None) -> None:
        self._voice = voice or _DEFAULT_VOICE

    async def synthesize(self, text: str, *, voice: str | None = None) -> bytes:
        chosen_voice = voice or self._voice
        # Lazy import — the package is heavy and brings ssl/aiohttp.
        try:
            import edge_tts  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover — install-time issue
            logger.error("edge-tts not installed: {}", safe_repr(exc))
            msg = "edge-tts not installed"
            raise TTSError(msg) from None

        try:
            communicate = edge_tts.Communicate(text, chosen_voice)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                # ``stream()`` yields dicts with ``type`` either "audio"
                # or "WordBoundary"; we want only the audio bytes.
                if isinstance(chunk, dict) and chunk.get("type") == "audio":
                    data: Any = chunk.get("data")
                    if isinstance(data, (bytes, bytearray)):
                        chunks.append(bytes(data))
        except Exception as exc:
            logger.warning("edge-tts synthesize failed: {}", safe_repr(exc))
            msg = "Edge TTS synthesis failed"
            raise TTSError(msg) from None

        if not chunks:
            msg = "Edge TTS returned no audio chunks"
            raise TTSError(msg)
        return b"".join(chunks)
