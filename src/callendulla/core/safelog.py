# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Best-effort secret redaction for log lines and ``repr(exc)`` strings.

This module is **not a security boundary**. It is a guardrail against the
specific failure mode of accidentally writing an upstream-library exception
to logs verbatim — ``httpx.HTTPStatusError.__repr__`` happily includes the
full request URL with ``?api_key=...`` and headers like
``Authorization: Bearer ...``.

The right thing to do is not log credentials at all. This module exists to
limit the blast radius when someone forgets to wrap their ``logger.error``
call with a sanitiser.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from loguru import Record

__all__ = ["install_loguru_redactor", "redact", "safe_repr"]

_REDACTED: Final[str] = "***"


def _compile(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


# Each entry: (pattern, replacement). Order matters — more specific keyed
# patterns run before raw shape detectors so we keep ``?api_key=`` prefix
# in the log line while only masking the value.
_REDACTORS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # URL query params / form fields: capture the key, replace value.
    # ``token`` is included as a bare alternative — in log lines, a raw
    # ``token=foo`` is overwhelmingly a credential. False positives on
    # CSRF/url-shortener "token" params are acceptable in exchange.
    (
        _compile(
            r"(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"id[_-]?token|auth[_-]?token|session[_-]?id|"
            r"telegram_bot_token|token|secret|password|pwd|passwd)"
            r"\s*[:=]\s*)"
            r"[^&\s'\";]+",
            re.IGNORECASE,
        ),
        rf"\1{_REDACTED}",
    ),
    # HTTP headers: keep header name + scheme, redact credential.
    (
        _compile(r"(\bauthorization\s*:\s*bearer\s+)\S+", re.IGNORECASE),
        rf"\1{_REDACTED}",
    ),
    (
        _compile(r"(\bauthorization\s*:\s*basic\s+)[A-Za-z0-9+/=]+", re.IGNORECASE),
        rf"\1{_REDACTED}",
    ),
    # Vendor-specific token shapes (after key=value rules so the prefix
    # is preserved when present, and otherwise we still catch them).
    # Anthropic must come before generic sk- because sk-ant- is a longer prefix.
    (_compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}\b"), _REDACTED),
    (_compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    (_compile(r"\bAIzaSy[A-Za-z0-9_-]{30,}\b"), _REDACTED),
    (_compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), _REDACTED),
    # Telegram Bot API token: <bot-id>:<base64url-like>. The strict shape
    # avoids matching version strings or timestamps.
    (_compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,40}\b"), _REDACTED),
    # Fernet symmetric key: base64-url, exactly 43 chars + literal '=',
    # not followed by another base64 char (else we'd eat half of a longer
    # blob).
    (_compile(r"\b[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])"), _REDACTED),
)


def redact(text: str) -> str:
    """Replace known secret patterns in ``text`` with ``***``.

    Idempotent: running on already-redacted text returns it unchanged. No
    guarantees about every conceivable token format; this catches the
    handful that we've actually seen leak in production-grade Python
    services (httpx/requests exception strings, env-dump on crash,
    ``repr(settings)`` in dev mode).
    """
    for pattern, replacement in _REDACTORS:
        text = pattern.sub(replacement, text)
    return text


def safe_repr(value: object) -> str:
    """``repr(value)`` with secret patterns redacted.

    Drop-in for ``logger.error(f"call failed: {exc!r}")`` style code, where
    ``exc`` might be an HTTP-client exception that carries a credential
    inside its ``__repr__``.
    """
    return redact(repr(value))


def install_loguru_redactor() -> None:
    """Patch loguru so :func:`redact` runs on every record's message.

    Call once at process start, ideally before any other logger setup. It
    is safe to call multiple times — :py:meth:`loguru.logger.configure`
    replaces the existing patcher rather than appending.

    Note: ``logger.bind(token=...)`` puts the value into ``record["extra"]``
    without going through the formatted message. We redact string values
    in ``extra`` here too, but the right discipline is *not to bind
    credentials* in the first place.
    """
    # Local import — loguru is a runtime dep but we avoid pulling it into
    # the import graph of callers that only use :func:`redact` /
    # :func:`safe_repr` (e.g. unit tests, scripts).
    from loguru import logger  # noqa: PLC0415

    def _patcher(record: Record) -> None:
        # Mutate in-place — loguru passes us the record dict.
        msg = record.get("message")
        if isinstance(msg, str):
            record["message"] = redact(msg)
        extra = record.get("extra")
        if isinstance(extra, Mapping):
            record["extra"] = {k: redact(v) if isinstance(v, str) else v for k, v in extra.items()}

    logger.configure(patcher=_patcher)
