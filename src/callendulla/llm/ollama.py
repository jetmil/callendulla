# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Ollama provider — local model server.

No API key required (this is the free / self-hosted path). Operator
points ``OLLAMA_BASE_URL`` at their server (default
``http://ollama:11434``).
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.llm.base import LLMError


class OllamaProvider:
    def __init__(self, *, base_url: str, model: str) -> None:
        self._base_url = base_url
        self._model = model
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from ollama import AsyncClient  # noqa: PLC0415

            self._client = AsyncClient(host=self._base_url)
        return self._client

    async def generate(self, prompt: str, *, max_tokens: int = 200) -> str:
        client = self._ensure_client()
        try:
            response = await client.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "num_predict": max_tokens,
                    "temperature": 0.9,
                },
            )
        except Exception as exc:
            logger.warning("ollama.chat failed: {}", safe_repr(exc))
            msg = "Ollama request failed"
            raise LLMError(msg) from None

        # ollama-python returns a dict-like with ['message']['content'].
        message = response.get("message") if isinstance(response, dict) else None
        if message is None:
            message = getattr(response, "message", None)
        if message is None:
            msg = "Ollama returned no message"
            raise LLMError(msg)
        content = (
            message.get("content", "")
            if isinstance(message, dict)
            else getattr(message, "content", "")
        )
        text = (content or "").strip()
        if not text:
            msg = "Ollama returned empty content"
            raise LLMError(msg)
        return text
