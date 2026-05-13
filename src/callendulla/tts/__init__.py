# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Text-to-speech (TTS) layer.

Operator picks an engine via ``TTS_ENGINE`` in ``.env``:

- ``edge`` — Microsoft Azure free endpoint via edge-tts. No key,
  but requires outbound HTTPS to Azure. The default.
- ``piper`` — fully local, no network. Requires voice-bank files
  on disk; not wired in this PR.
- ``cosyvoice`` — premium, needs a separate self-hosted server.
  Not wired in this PR.

Returns OGG/Opus audio bytes ready for :py:meth:`Bot.send_voice`.
Like STT, returning ``None`` from the factory is first-class — diary
just stays in text mode in that case.
"""

from callendulla.tts.base import TTSError, TTSProvider
from callendulla.tts.factory import build_tts_provider

__all__ = ["TTSError", "TTSProvider", "build_tts_provider"]
