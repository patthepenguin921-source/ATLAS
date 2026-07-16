"""App-level encryption for credentials that must be stored at rest.

Atlas has no external secret vault yet, so integration credentials (e.g. a
PowerSchool portal login) are encrypted with a server-only key before being
written to `integrations.secret_ref` — the backend can decrypt them for a
sync, but the ciphertext alone is useless without ATLAS_SECRET_KEY, which
never leaves the environment.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CryptoError(RuntimeError):
    pass


@lru_cache
def _fernet() -> Fernet:
    if not settings.atlas_secret_key:
        raise CryptoError(
            "ATLAS_SECRET_KEY is not set — required to store or read encrypted "
            "integration credentials. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(settings.atlas_secret_key.encode())


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except InvalidToken as e:
        raise CryptoError("Stored credentials could not be decrypted (wrong or rotated key?).") from e
