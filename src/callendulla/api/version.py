# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Build identity served by the ``/source`` AGPL §13 endpoint.

Resolves the commit SHA from one of:

1. ``CALLENDULLA_COMMIT_SHA`` env (set during ``docker build`` —
   see ``Dockerfile``)
2. local ``.git/HEAD`` lookup (development case)
3. literal ``"unknown"`` (fallback — operator still gets the URL)
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Final

from callendulla._version import __version__

_UNKNOWN_SHA: Final[str] = "unknown"


def _read_git_head(repo_root: Path) -> str:
    """Walk ``.git/HEAD`` → ``refs/heads/<branch>`` → SHA file.

    Returns ``"unknown"`` on any error rather than raising. Called at
    process start; we don't want a missing ``.git`` to crash boot.
    """
    git_dir = repo_root / ".git"
    head_file = git_dir / "HEAD"
    if not head_file.is_file():
        return _UNKNOWN_SHA
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return _UNKNOWN_SHA
    if head_content.startswith("ref: "):
        ref_path = git_dir / head_content[5:]
        if ref_path.is_file():
            try:
                return ref_path.read_text(encoding="utf-8").strip()[:40]
            except OSError:
                return _UNKNOWN_SHA
        return _UNKNOWN_SHA
    # Detached HEAD — file contains a SHA directly.
    return head_content[:40] or _UNKNOWN_SHA


def _detect_repo_root() -> Path:
    # Walk up from this file until we find ``.git`` or hit FS root.
    cursor = Path(__file__).resolve().parent
    for parent in (cursor, *cursor.parents):
        if (parent / ".git").exists():
            return parent
    return cursor  # no .git anywhere — caller falls back to "unknown"


@lru_cache(maxsize=1)
def commit_sha() -> str:
    """Best-effort 40-char commit SHA. Cached for the process lifetime."""
    explicit = os.environ.get("CALLENDULLA_COMMIT_SHA", "").strip()
    if explicit:
        return explicit[:40]
    return _read_git_head(_detect_repo_root())


@lru_cache(maxsize=1)
def build_date() -> str:
    """Best-effort build timestamp.

    Prefers ``CALLENDULLA_BUILD_DATE`` (set during ``docker build``).
    Falls back to process-start time when running un-containerised —
    less accurate, but always something.
    """
    explicit = os.environ.get("CALLENDULLA_BUILD_DATE", "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).isoformat()


def package_version() -> str:
    """Semver-ish version baked into the wheel / source tree."""
    return __version__
