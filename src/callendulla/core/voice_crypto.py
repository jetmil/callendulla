# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Darovitsky <jetmil@proton.me>
"""Fernet wrapper for at-rest encryption of voice-diary blobs.

Single key per deployment, loaded from ``Settings.diary_encryption_key``
which is already validated as a valid Fernet token at startup. Rotation
needs a re-encrypt script (``docs/key-rotation.md`` — to be written);
this module deliberately keeps the API minimal so that script will be
the only place that knows about multi-key state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from pydantic import SecretStr


class DecryptionError(Exception):
    """Raised when ciphertext cannot be decrypted with the current key.

    Almost always means the key was rotated and this row hasn't been
    re-encrypted yet. The caller should NOT include the original
    exception message in any user-facing response — it carries
    cryptographic hints.
    """


def _fernet_from_key(key: SecretStr) -> Fernet:
    return Fernet(key.get_secret_value().encode())


def encrypt(plaintext: bytes | bytearray, *, key: SecretStr) -> bytes:
    """Encrypt with the operator's Fernet key.

    Returns the URL-safe-base64 Fernet token bytes — store as
    :class:`bytes` LargeBinary.
    """
    return _fernet_from_key(key).encrypt(bytes(plaintext))


def decrypt(ciphertext: bytes, *, key: SecretStr) -> bytes:
    """Reverse of :func:`encrypt`.

    Raises :class:`DecryptionError` (not the upstream :class:`InvalidToken`)
    so callers can produce a sanitised user response.
    """
    try:
        return _fernet_from_key(key).decrypt(ciphertext)
    except InvalidToken as exc:
        msg = "ciphertext does not match the current DIARY_ENCRYPTION_KEY"
        raise DecryptionError(msg) from exc
