# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Pick an STT implementation based on :class:`Settings`.

Strategy: STT piggybacks on the LLM provider's key when the provider
also offers transcription. Today that's OpenAI (Whisper). Other LLM
providers without first-party STT (Anthropic, Gemini-Native, Ollama)
return ``None`` — the diary persists audio without a transcript, and
the user can play it back any time. Local ``whisper-cpp`` will land as
a separate ``ProviderKind`` in a follow-up so it works without an
LLM_API_KEY at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from callendulla.config import LLMProvider as ProviderKind

if TYPE_CHECKING:
    from callendulla.config import Settings
    from callendulla.stt.base import STTProvider


def build_stt_provider(settings: Settings) -> STTProvider | None:
    """Construct an STT adapter, or ``None`` when STT is unavailable.

    Currently only the OpenAI Whisper path is wired. Returning ``None``
    is a first-class state: callers MUST handle it as "no transcription,
    audio-only diary".
    """
    if settings.llm_provider is ProviderKind.OPENAI and settings.llm_api_key is not None:
        from callendulla.stt.openai_whisper import OpenAIWhisperProvider  # noqa: PLC0415

        return OpenAIWhisperProvider(
            api_key=settings.llm_api_key.get_secret_value(),
        )

    # Future: ProviderKind.WHISPER_LOCAL — runs whisper-cpp.
    return None
