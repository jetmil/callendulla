# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""LLM provider layer — BYOK (Bring Your Own Key).

The project ships no API keys, ever. Each operator configures
:envvar:`LLM_PROVIDER` and :envvar:`LLM_API_KEY` in their own ``.env``;
the factory picks one provider at startup. CI never makes real calls —
all tests mock the SDKs.

Public surface:

- :class:`LLMProvider` — Protocol every implementation satisfies
- :class:`LLMError` — single error type providers raise on failure
- :func:`build_provider` — picks the right implementation from
  :class:`callendulla.config.Settings`
- :func:`compose_nudge_prompt` — turns a (profile, tone, title) tuple
  into a prompt; same composer for every provider
"""

from callendulla.llm.base import LLMError, LLMProvider
from callendulla.llm.factory import build_provider
from callendulla.llm.prompt import compose_nudge_prompt

__all__ = [
    "LLMError",
    "LLMProvider",
    "build_provider",
    "compose_nudge_prompt",
]
