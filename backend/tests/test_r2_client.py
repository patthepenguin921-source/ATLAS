"""R2 upload URL-component validation — a control character (a stray
newline pasted into an env var being the real production case) anywhere in
the account id, bucket, or storage key breaks httpx's URL parsing with a
generic "Invalid non-printable ASCII character in URL" that doesn't say
which component is at fault. `_check_url_components` pins that down before
the request is even made.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.r2_client import R2Client, R2Error, _control_char_position, safe_object_name


def test_control_char_position_finds_a_newline():
    assert _control_char_position("abc123\n") == 6


def test_control_char_position_none_for_clean_string():
    assert _control_char_position("abc123.pdf") is None


def test_safe_object_name_strips_newlines_from_filenames():
    assert safe_object_name("abc\ndef.pdf") == "abc_def.pdf"


def _client_with(account_id: str = "clean-account", bucket: str = "clean-bucket") -> R2Client:
    client = R2Client.__new__(R2Client)
    client._account_id = account_id
    client._access_key = "key"
    client._secret_key = "secret"
    client._bucket = bucket
    client._host = f"{account_id}.r2.cloudflarestorage.com"
    client._client = None
    return client


def test_upload_reports_account_id_as_the_culprit():
    client = _client_with(account_id="abc123\n")
    with pytest.raises(R2Error, match="account id"):
        asyncio.run(client.upload("some/key.pdf", b"data", "application/pdf"))


def test_upload_reports_bucket_as_the_culprit():
    client = _client_with(bucket="atlas-documents\n")
    with pytest.raises(R2Error, match="bucket"):
        asyncio.run(client.upload("some/key.pdf", b"data", "application/pdf"))


def test_upload_reports_key_as_the_culprit():
    client = _client_with()
    with pytest.raises(R2Error, match="key"):
        asyncio.run(client.upload("some/key\n.pdf", b"data", "application/pdf"))


def test_upload_error_never_includes_the_raw_value():
    client = _client_with(account_id="super-secret-account-id\n")
    with pytest.raises(R2Error) as exc_info:
        asyncio.run(client.upload("some/key.pdf", b"data", "application/pdf"))
    assert "super-secret-account-id" not in str(exc_info.value)
