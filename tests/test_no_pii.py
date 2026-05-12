# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Repository PII / private-infra guard.

Walks every tracked file and asserts that no banned substring appears.
This is a defence in depth — the primary guard is reviewer discipline,
secondary is ``gitleaks`` in CI (which catches *token* shapes). This
test catches strings that look benign in isolation but identify the
author's private deployment when correlated.

Adding to ``BANNED`` is cheap, removing must be deliberate. When a string
genuinely needs to appear in source (e.g. a third-party domain in a doc
example), add the specific file to ``ALLOWED_FILES``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Substrings that must never appear in a public commit. The pattern is
# author-deployment-specific; forks should add their own and remove these
# if their reviewers want a different scanner.
BANNED: tuple[str, ...] = (
    # private deployment paths
    "/var/www",
    # private domain root
    "ligardi.ru",
    "art-svechi.ru",
    # private IPs (home WSL, Aeza VPS, WSL bridge)
    "37.79.241.86",
    "85.192.63.21",
    "192.168.0.95",
)

# Files that legitimately contain a banned substring (e.g. this test file
# itself lists them as literals).
ALLOWED_FILES: frozenset[str] = frozenset(
    {
        "tests/test_no_pii.py",
    }
)


def _tracked_files() -> list[Path]:
    """Return every git-tracked file as an absolute path."""
    out = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line]


@pytest.mark.parametrize("banned", BANNED)
def test_banned_substring_not_in_repo(banned: str) -> None:
    hits: list[str] = []
    for path in _tracked_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWED_FILES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # binary or unreadable — skip; gitleaks covers binary blobs
            continue
        if banned in content:
            hits.append(rel)
    assert not hits, f"banned substring {banned!r} found in: {hits}"
