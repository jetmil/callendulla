# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.core.safelog`."""

from __future__ import annotations

import pytest

from callendulla.core.safelog import install_loguru_redactor, redact, safe_repr

# Synthetic, well-formed secrets that match each detector. They are not
# valid for any real service. Keep them long enough to satisfy the
# minimum-length guards but mark them with obvious prefixes ('FAKE') so a
# human reviewing failed-test output knows these are not live keys.
_FAKE_OPENAI = "sk-FAKEFAKEFAKEFAKEFAKEFAKE0000"
_FAKE_ANTHROPIC = "sk-ant-FAKEFAKEFAKEFAKEFAKE00"
_FAKE_GOOGLE = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE00"
_FAKE_GITHUB_PAT = "ghp_FAKEFAKEFAKEFAKEFAKEFAKE0000000"
_FAKE_GITHUB_OAUTH = "gho_FAKEFAKEFAKEFAKEFAKEFAKE0000000"
_FAKE_TELEGRAM = "1234567890:AAFAKEFAKEFAKEFAKEFAKEFAKE000000"
_FAKE_FERNET = "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK="


class TestRedactKnownPatterns:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # ── URL query / form key=value ─────────────────────────
            (
                "https://api.example.com/v1?api_key=secret123abc",
                "https://api.example.com/v1?api_key=***",
            ),
            (
                "https://api.example.com/v1?API_KEY=secret123abc",
                "https://api.example.com/v1?API_KEY=***",
            ),
            ("token=foobarbaz&user=alice", "token=***&user=alice"),
            (
                "password=hunter2 username=alice",
                "password=*** username=alice",
            ),
            (
                "telegram_bot_token=fake-12345-abc",
                "telegram_bot_token=***",
            ),
            # ── HTTP headers ───────────────────────────────────────
            (
                "Authorization: Bearer eyJhbGc.foo.bar",
                "Authorization: Bearer ***",
            ),
            (
                "authorization: bearer xyz",
                "authorization: bearer ***",
            ),
            (
                "Authorization: Basic dXNlcjpwYXNz",
                "Authorization: Basic ***",
            ),
            # ── Vendor token shapes ────────────────────────────────
            (f"key is {_FAKE_OPENAI} here", "key is *** here"),
            (f"key is {_FAKE_ANTHROPIC} here", "key is *** here"),
            (f"key=={_FAKE_GOOGLE}", "key==***"),
            (f"GH token: {_FAKE_GITHUB_PAT}", "GH token: ***"),
            (f"oauth: {_FAKE_GITHUB_OAUTH}", "oauth: ***"),
            (f"BOT={_FAKE_TELEGRAM} END", "BOT=*** END"),
            # ── Fernet key ─────────────────────────────────────────
            (f"export DIARY_KEY={_FAKE_FERNET}", "export DIARY_KEY=***"),
        ],
    )
    def test_pattern_redacted(self, raw: str, expected: str) -> None:
        assert redact(raw) == expected

    def test_anthropic_more_specific_than_openai(self) -> None:
        """Both prefixes match the input, but only one ``***`` should remain."""
        redacted = redact(_FAKE_ANTHROPIC)
        assert redacted == "***"


class TestRedactBenignStrings:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "hello world",
            "version v1.2.3-rc4 deployed",
            # 'sk-' but too short to be a real OpenAI key
            "filename: sk-short.txt",
            # path that contains digits and colons but not in token shape
            "/proc/12345:fd/3",
            # ISO timestamp must not be redacted
            "2026-05-12T07:30:00Z",
            # Russian text doesn't trip patterns
            "Бот успешно зарегистрирован",
        ],
    )
    def test_unchanged(self, text: str) -> None:
        assert redact(text) == text


class TestRedactIdempotent:
    def test_double_redact_stable(self) -> None:
        once = redact(f"x={_FAKE_OPENAI} y=Bearer abc")
        twice = redact(once)
        assert once == twice


class TestSafeRepr:
    def test_repr_with_token_redacted(self) -> None:
        class _FakeError(Exception):
            def __repr__(self) -> str:
                return f"FakeError(url='https://api/?api_key={_FAKE_OPENAI}')"

        text = safe_repr(_FakeError())
        assert "api_key=***" in text
        assert _FAKE_OPENAI not in text

    def test_repr_of_plain_value_unchanged(self) -> None:
        assert safe_repr(42) == "42"
        assert safe_repr("hello") == "'hello'"


class TestLoguruRedactor:
    def test_install_does_not_raise(self) -> None:
        """Smoke test — actual log-output assertion requires capsys+loguru sink.

        We don't want a per-test global loguru reconfigure to bleed into
        other tests, so the deep assertion (logs are redacted in flight)
        lives in ``tests/integration/test_logging.py`` and runs only
        when explicitly selected.
        """
        install_loguru_redactor()
        install_loguru_redactor()  # idempotent
