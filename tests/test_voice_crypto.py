# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Tests for :mod:`callendulla.core.voice_crypto`."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from callendulla.core.voice_crypto import DecryptionError, decrypt, encrypt


def _key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


class TestRoundtrip:
    @pytest.mark.parametrize(
        "plaintext",
        [
            b"",
            b"hello world",
            bytes(range(256)),  # every byte value
            b"\x00" * 10_000,  # large input with nulls
        ],
    )
    def test_decrypt_inverts_encrypt(self, plaintext: bytes) -> None:
        key = _key()
        ct = encrypt(plaintext, key=key)
        assert decrypt(ct, key=key) == plaintext

    def test_ciphertext_differs_from_plaintext(self) -> None:
        key = _key()
        plain = b"diary entry contents"
        ct = encrypt(plain, key=key)
        assert ct != plain
        assert plain not in ct  # not lurking anywhere inside

    def test_repeated_encrypt_yields_different_ciphertext(self) -> None:
        """Fernet uses a random IV — same plaintext, same key → different
        ciphertext. Important: catches accidental ECB-like reuse."""
        key = _key()
        plain = b"same input every time"
        ct1 = encrypt(plain, key=key)
        ct2 = encrypt(plain, key=key)
        assert ct1 != ct2
        # But both decrypt back to the same thing
        assert decrypt(ct1, key=key) == decrypt(ct2, key=key) == plain


class TestKeyMismatch:
    def test_wrong_key_raises_decryption_error(self) -> None:
        k1 = _key()
        k2 = _key()
        ct = encrypt(b"secret", key=k1)
        with pytest.raises(DecryptionError):
            decrypt(ct, key=k2)

    def test_decryption_error_message_does_not_leak_key_material(self) -> None:
        """If we ever start showing this to users, ensure it never carries
        the key value. The current message references the env name only."""
        k1 = _key()
        k2 = _key()
        ct = encrypt(b"secret", key=k1)
        with pytest.raises(DecryptionError) as exc_info:
            decrypt(ct, key=k2)
        text = str(exc_info.value)
        assert k1.get_secret_value() not in text
        assert k2.get_secret_value() not in text


class TestTypeChecks:
    def test_encrypt_rejects_str(self) -> None:
        with pytest.raises(TypeError):
            encrypt("not bytes", key=_key())  # type: ignore[arg-type]

    def test_encrypt_accepts_bytearray(self) -> None:
        key = _key()
        ct = encrypt(bytearray(b"abc"), key=key)
        assert decrypt(ct, key=key) == b"abc"
