# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Gemini provider — wraps google-genai.

BYOK: ``api_key`` MUST come from operator config, never from the
repository. Activated only when ``LLM_PROVIDER=gemini`` in ``.env``.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from callendulla.core.safelog import safe_repr
from callendulla.llm.base import LLMError


class GeminiProvider:
    """Adapter over :mod:`google.genai`.

    The SDK is imported lazily so importing this module doesn't pull
    google-genai into the graph of callers that just want
    :class:`LLMProvider` for typing.
    """

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            # ``google-genai`` ships as a namespace package — mypy does
            # not always see ``genai`` as an attribute of ``google``.
            from google import genai  # type: ignore[attr-defined,import-untyped]  # noqa: PLC0415

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str, *, max_tokens: int = 200) -> str:
        client = self._ensure_client()
        try:
            response = await client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config={
                    "max_output_tokens": max_tokens,
                    "temperature": 0.9,
                },
            )
        except Exception as exc:  # SDK can raise any of dozens of types
            logger.warning("gemini.generate failed: {}", safe_repr(exc))
            msg = "Gemini request failed"
            raise LLMError(msg) from None

        # ``response.text`` is a property that raises on tool-call /
        # safety-block responses. Walk parts manually instead.
        text = _extract_text(response)
        if not text:
            msg = "Gemini returned empty text"
            raise LLMError(msg)
        return text


def _extract_text(response: Any) -> str:
    """Best-effort text concatenation across all parts.

    Tool-calls and safety blocks return non-text parts; we ignore them
    and return whatever plain-text content is there (often empty for a
    safety-blocked completion).
    """
    candidates = getattr(response, "candidates", None) or []
    chunks: list[str] = []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if content is None:
            continue
        for part in getattr(content, "parts", None) or []:
            value = getattr(part, "text", None)
            if isinstance(value, str) and value.strip():
                chunks.append(value)
    return "\n".join(chunks).strip()
