# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.bot.keyboards`."""

from __future__ import annotations

import pytest

from callendulla.bot.keyboards import (
    CALLBACK_PREFIX,
    NudgeAction,
    build_callback_data,
    nudge_keyboard,
    parse_callback_data,
)


class TestCallbackEncoding:
    @pytest.mark.parametrize("action", list(NudgeAction))
    def test_roundtrip(self, action: NudgeAction) -> None:
        encoded = build_callback_data(12345, action)
        decoded = parse_callback_data(encoded)
        assert decoded == (12345, action)

    def test_telegram_64byte_limit(self) -> None:
        for action in NudgeAction:
            # Worst case: id at max bigint width
            encoded = build_callback_data(2**62 - 1, action)
            assert len(encoded.encode("utf-8")) <= 64

    def test_starts_with_prefix(self) -> None:
        encoded = build_callback_data(1, NudgeAction.ACK)
        assert encoded.startswith(CALLBACK_PREFIX + ":")


class TestParseMalformed:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "garbage",
            "nudge:onlytwoparts",
            "other:1:ack",  # wrong prefix
            "nudge:notanint:ack",
            "nudge:1:not_an_action",
            "nudge:1:ack:extra",  # too many parts
        ],
    )
    def test_malformed_returns_none(self, raw: str) -> None:
        assert parse_callback_data(raw) is None


class TestNudgeKeyboard:
    def test_two_rows_four_buttons(self) -> None:
        kb = nudge_keyboard(42)
        # 2 rows of 2 buttons
        assert len(kb.inline_keyboard) == 2
        assert all(len(row) == 2 for row in kb.inline_keyboard)
        all_buttons = [b for row in kb.inline_keyboard for b in row]
        assert len(all_buttons) == len(NudgeAction)

    def test_every_action_present(self) -> None:
        kb = nudge_keyboard(42)
        actions_seen: set[NudgeAction] = set()
        for row in kb.inline_keyboard:
            for btn in row:
                parsed = parse_callback_data(btn.callback_data or "")
                assert parsed is not None
                actions_seen.add(parsed[1])
        assert actions_seen == set(NudgeAction)

    def test_id_embedded_in_each_button(self) -> None:
        kb = nudge_keyboard(999)
        for row in kb.inline_keyboard:
            for btn in row:
                parsed = parse_callback_data(btn.callback_data or "")
                assert parsed is not None
                nudge_id, _ = parsed
                assert nudge_id == 999
