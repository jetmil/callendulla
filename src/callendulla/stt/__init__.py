# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Speech-to-text (STT) layer.

Same BYOK discipline as :mod:`callendulla.llm` — operator's key, never
ours. Currently shipping one provider (OpenAI Whisper API); local
``whisper-cpp`` lands later. Public surface:

- :class:`STTProvider` — Protocol every adapter satisfies
- :class:`STTError` — single error type for graceful fallback
- :func:`build_stt_provider` — returns a provider or ``None`` when STT
  is unavailable / not configured. ``None`` means the diary works
  without transcripts — voice still stored, transcript field is an
  encrypted empty placeholder.
"""

from callendulla.stt.base import STTError, STTProvider
from callendulla.stt.factory import build_stt_provider

__all__ = ["STTError", "STTProvider", "build_stt_provider"]
