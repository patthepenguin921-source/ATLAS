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

from app.core import r2_client as r2_client_module
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


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        return self._response


def _enable_r2(monkeypatch):
    """`enabled`/`_require()` check the global `settings`, not the fields
    the `_client_with()` fixture sets directly on the instance — patch them
    too for tests that need to get past `_require()`."""
    monkeypatch.setattr(r2_client_module.settings, "r2_account_id", "clean-account")
    monkeypatch.setattr(r2_client_module.settings, "r2_access_key_id", "key")
    monkeypatch.setattr(r2_client_module.settings, "r2_secret_access_key", "secret")


def test_list_objects_parses_keys_and_pagination(monkeypatch):
    _enable_r2(monkeypatch)
    client = _client_with()
    xml = (
        "<ListBucketResult>"
        "<Key>a/b.pdf</Key><Key>c/d.pdf</Key>"
        "<IsTruncated>true</IsTruncated>"
        "<NextContinuationToken>tok123</NextContinuationToken>"
        "</ListBucketResult>"
    )
    fake_http = _FakeHttpClient(_FakeResponse(200, xml))
    client._client = fake_http

    result = asyncio.run(client.list_objects())

    assert result == {"keys": ["a/b.pdf", "c/d.pdf"], "next_token": "tok123"}
    assert len(fake_http.calls) == 1
    url, headers = fake_http.calls[0]
    assert url.startswith("https://clean-account.r2.cloudflarestorage.com/clean-bucket?")
    assert "Authorization" in headers


def test_list_objects_no_next_token_when_not_truncated(monkeypatch):
    _enable_r2(monkeypatch)
    client = _client_with()
    xml = "<ListBucketResult><Key>only.pdf</Key><IsTruncated>false</IsTruncated></ListBucketResult>"
    client._client = _FakeHttpClient(_FakeResponse(200, xml))

    result = asyncio.run(client.list_objects())

    assert result == {"keys": ["only.pdf"], "next_token": None}


def test_list_objects_raises_r2error_on_failure(monkeypatch):
    _enable_r2(monkeypatch)
    client = _client_with()
    client._client = _FakeHttpClient(_FakeResponse(403, "Forbidden"))

    with pytest.raises(R2Error):
        asyncio.run(client.list_objects())
