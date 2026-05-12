# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""OpenAI provider — wraps openai SDK.

BYOK only — no keys in this repo. Tests mock the SDK; CI never makes
real calls.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.llm.base import LLMError


class OpenAIProvider:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI  # noqa: PLC0415

            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str, *, max_tokens: int = 200) -> str:
        client = self._ensure_client()
        try:
            response = await client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.9,
            )
        except Exception as exc:
            logger.warning("openai.chat failed: {}", safe_repr(exc))
            msg = "OpenAI request failed"
            raise LLMError(msg) from None

        choices = getattr(response, "choices", None) or []
        if not choices:
            msg = "OpenAI returned no choices"
            raise LLMError(msg)
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", "") or ""
        text = text.strip()
        if not text:
            msg = "OpenAI returned empty content"
            raise LLMError(msg)
        return text
