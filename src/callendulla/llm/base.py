# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""LLM provider Protocol + shared error type.

A provider is just ``async def generate(prompt, *, max_tokens) -> str``.
Anything richer (function-calling, streaming) is not used by the
nudge engine — keeping the contract narrow lets each adapter stay
small and lets us stub it in tests with a single line.
"""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """One error type every provider raises on failure.

    The scheduler's nudge engine catches this and falls back to the
    template bank. Network / auth / rate-limit / parser problems all
    arrive here — caller does not need to discriminate.
    """


class LLMProvider(Protocol):
    """The shape callers depend on."""

    async def generate(self, prompt: str, *, max_tokens: int = 200) -> str:
        """Return the model's reply text.

        Implementations MUST raise :class:`LLMError` for any failure,
        including network timeouts. Never let an upstream exception
        leak — its ``repr`` may contain the API key.
        """
        ...
