# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""OpenAI Whisper provider — ``whisper-1`` over HTTPS.

BYOK only. Reuses the operator's ``LLM_API_KEY`` when
``LLM_PROVIDER=openai`` — the same key works for both endpoints,
saves the operator from adding a second one.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.stt.base import STTError


class OpenAIWhisperProvider:
    """Adapter over :mod:`openai`'s audio.transcriptions endpoint."""

    def __init__(self, *, api_key: str, model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI  # noqa: PLC0415

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        fmt: str = "ogg",
        language: str | None = None,
    ) -> str:
        client = self._ensure_client()
        # OpenAI SDK expects a file-like with a ``.name`` attribute it
        # uses to derive the format hint.
        bio = BytesIO(audio_bytes)
        bio.name = f"audio.{fmt}"

        try:
            kwargs: dict[str, Any] = {"model": self._model, "file": bio}
            if language:
                kwargs["language"] = language
            response = await client.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            logger.warning("whisper.transcribe failed: {}", safe_repr(exc))
            msg = "Whisper transcription failed"
            raise STTError(msg) from None

        text = getattr(response, "text", "")
        if not isinstance(text, str) or not text.strip():
            msg = "Whisper returned empty transcript"
            raise STTError(msg)
        return text.strip()
