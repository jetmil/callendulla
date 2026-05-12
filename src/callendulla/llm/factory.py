# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Pick the right :class:`LLMProvider` implementation from settings.

Called once at scheduler startup. The factory does not validate that
the key actually works — that's discovered on first ``generate`` call
and surfaced via :class:`LLMError`, after which the engine falls
back to the template bank.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from callendulla.config import LLMProvider as ProviderKind

if TYPE_CHECKING:
    from callendulla.config import Settings
    from callendulla.llm.base import LLMProvider


def build_provider(settings: Settings) -> LLMProvider:
    """Construct the provider matching ``settings.llm_provider``.

    Lazy imports keep the SDK out of the import graph for providers we
    aren't using. A misconfig (``openai`` selected, ``LLM_API_KEY``
    empty) raises :class:`ValueError` here at startup — better than a
    silent fallback to templates that hides a real bug.
    """
    kind = settings.llm_provider

    if kind is ProviderKind.GEMINI:
        if settings.llm_api_key is None:
            msg = "LLM_PROVIDER=gemini requires LLM_API_KEY"
            raise ValueError(msg)
        from callendulla.llm.gemini import GeminiProvider  # noqa: PLC0415

        return GeminiProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )

    if kind is ProviderKind.OPENAI:
        if settings.llm_api_key is None:
            msg = "LLM_PROVIDER=openai requires LLM_API_KEY"
            raise ValueError(msg)
        from callendulla.llm.openai import OpenAIProvider  # noqa: PLC0415

        return OpenAIProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )

    if kind is ProviderKind.ANTHROPIC:
        if settings.llm_api_key is None:
            msg = "LLM_PROVIDER=anthropic requires LLM_API_KEY"
            raise ValueError(msg)
        from callendulla.llm.anthropic import AnthropicProvider  # noqa: PLC0415

        return AnthropicProvider(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
        )

    if kind is ProviderKind.OLLAMA:
        from callendulla.llm.ollama import OllamaProvider  # noqa: PLC0415

        base_url = (
            str(settings.ollama_base_url) if settings.ollama_base_url else "http://ollama:11434"
        )
        return OllamaProvider(base_url=base_url, model=settings.llm_model)

    # Defensive: every value of the StrEnum is handled above, so mypy
    # marks this branch unreachable. We keep it as a guard against
    # someone adding a new provider variant and forgetting the factory.
    msg = f"unknown LLM_PROVIDER={kind!r}"  # type: ignore[unreachable]
    raise ValueError(msg)
