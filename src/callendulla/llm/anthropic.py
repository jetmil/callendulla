# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Anthropic provider — wraps anthropic SDK.

BYOK only.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.llm.base import LLMError


class AnthropicProvider:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import AsyncAnthropic  # noqa: PLC0415

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str, *, max_tokens: int = 200) -> str:
        client = self._ensure_client()
        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=0.9,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.warning("anthropic.messages failed: {}", safe_repr(exc))
            msg = "Anthropic request failed"
            raise LLMError(msg) from None

        # ``response.content`` is a list of content blocks; we want the
        # text from text-type blocks only.
        chunks: list[str] = []
        for block in getattr(response, "content", None) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                value = getattr(block, "text", "")
                if value:
                    chunks.append(value)
        text = "\n".join(chunks).strip()
        if not text:
            msg = "Anthropic returned empty content"
            raise LLMError(msg)
        return text
