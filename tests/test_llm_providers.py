# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Adapter-level tests for each LLM provider.

Real SDK calls are forbidden — every test mocks the underlying client
through ``patch`` on the lazy ``_ensure_client`` factory. We verify:

- happy path returns the model's text
- API exception is wrapped in :class:`LLMError`
- empty / non-text response is wrapped in :class:`LLMError`
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callendulla.llm.anthropic import AnthropicProvider
from callendulla.llm.base import LLMError
from callendulla.llm.gemini import GeminiProvider
from callendulla.llm.ollama import OllamaProvider
from callendulla.llm.openai import OpenAIProvider

# ─── Gemini ─────────────────────────────────────────────────────────


def _fake_gemini_response(text: str | None) -> Any:
    part = MagicMock()
    part.text = text or ""
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    resp = MagicMock()
    resp.candidates = [cand]
    return resp


class TestGemini:
    async def test_happy_path(self) -> None:
        provider = GeminiProvider(api_key="K", model="gemini-2.5-flash")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            return_value=_fake_gemini_response("привет от модели")
        )
        provider._client = client  # type: ignore[attr-defined]

        text = await provider.generate("prompt")
        assert text == "привет от модели"

    async def test_sdk_exception_becomes_llm_error(self) -> None:
        provider = GeminiProvider(api_key="K", model="gemini-2.5-flash")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("net"))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")

    async def test_empty_response_becomes_llm_error(self) -> None:
        provider = GeminiProvider(api_key="K", model="gemini-2.5-flash")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(return_value=_fake_gemini_response(""))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")


# ─── OpenAI ─────────────────────────────────────────────────────────


def _fake_openai_response(text: str | None) -> Any:
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestOpenAI:
    async def test_happy_path(self) -> None:
        provider = OpenAIProvider(api_key="K", model="gpt-4o-mini")
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            return_value=_fake_openai_response("ответ openai")
        )
        provider._client = client  # type: ignore[attr-defined]

        text = await provider.generate("prompt")
        assert text == "ответ openai"

    async def test_sdk_exception_becomes_llm_error(self) -> None:
        provider = OpenAIProvider(api_key="K", model="gpt-4o-mini")
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("net"))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")

    async def test_empty_choice_becomes_llm_error(self) -> None:
        provider = OpenAIProvider(api_key="K", model="gpt-4o-mini")
        client = MagicMock()
        resp = MagicMock()
        resp.choices = []
        client.chat.completions.create = AsyncMock(return_value=resp)
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")


# ─── Anthropic ──────────────────────────────────────────────────────


def _fake_anthropic_response(text: str | None) -> Any:
    block = MagicMock()
    block.type = "text"
    block.text = text or ""
    resp = MagicMock()
    resp.content = [block]
    return resp


class TestAnthropic:
    async def test_happy_path(self) -> None:
        provider = AnthropicProvider(api_key="K", model="claude-haiku-4-5-20251001")
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=_fake_anthropic_response("ответ anthropic"))
        provider._client = client  # type: ignore[attr-defined]
        text = await provider.generate("prompt")
        assert text == "ответ anthropic"

    async def test_non_text_block_ignored(self) -> None:
        provider = AnthropicProvider(api_key="K", model="claude-haiku-4-5-20251001")
        client = MagicMock()
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "хорошо"
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        resp = MagicMock()
        resp.content = [tool_block, text_block]
        client.messages.create = AsyncMock(return_value=resp)
        provider._client = client  # type: ignore[attr-defined]
        assert await provider.generate("prompt") == "хорошо"

    async def test_sdk_exception_becomes_llm_error(self) -> None:
        provider = AnthropicProvider(api_key="K", model="claude-haiku-4-5-20251001")
        client = MagicMock()
        client.messages.create = AsyncMock(side_effect=RuntimeError("net"))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")


# ─── Ollama ─────────────────────────────────────────────────────────


class TestOllama:
    async def test_happy_path(self) -> None:
        provider = OllamaProvider(base_url="http://ollama:11434", model="gemma3:12b")
        client = MagicMock()
        client.chat = AsyncMock(return_value={"message": {"content": "локальный ответ"}})
        provider._client = client  # type: ignore[attr-defined]
        text = await provider.generate("prompt")
        assert text == "локальный ответ"

    async def test_sdk_exception_becomes_llm_error(self) -> None:
        provider = OllamaProvider(base_url="http://ollama:11434", model="gemma3:12b")
        client = MagicMock()
        client.chat = AsyncMock(side_effect=RuntimeError("conn refused"))
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")

    async def test_empty_content_becomes_llm_error(self) -> None:
        provider = OllamaProvider(base_url="http://ollama:11434", model="gemma3:12b")
        client = MagicMock()
        client.chat = AsyncMock(return_value={"message": {"content": ""}})
        provider._client = client  # type: ignore[attr-defined]
        with pytest.raises(LLMError):
            await provider.generate("prompt")


# ─── Cross-cutting: no real keys leak via repr ──────────────────────


class TestSecretRepr:
    """``repr`` of a provider must NOT expose the API key.

    The provider stores it in a private attribute; default ``repr`` is
    fine because it doesn't introspect attributes. This test fails the
    day someone adds a __repr__ that dumps state."""

    def test_gemini_repr(self) -> None:
        text = repr(GeminiProvider(api_key="SECRET-XXX", model="m"))
        assert "SECRET-XXX" not in text

    def test_openai_repr(self) -> None:
        text = repr(OpenAIProvider(api_key="SECRET-XXX", model="m"))
        assert "SECRET-XXX" not in text

    def test_anthropic_repr(self) -> None:
        text = repr(AnthropicProvider(api_key="SECRET-XXX", model="m"))
        assert "SECRET-XXX" not in text
