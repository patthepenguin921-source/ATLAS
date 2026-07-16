"""PowerSchool login handshake + scraping, exercised against a fake portal
(httpx.MockTransport) since there's no live PowerSchool instance to test
against. This proves the plumbing works end-to-end for HTML shaped like a
real portal's; a real district's exact markup may still need small selector
tweaks (see `powerschool_client.py`'s module docstring).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.crypto import CryptoError, decrypt_json, encrypt_json
from app.integrations.powerschool_client import (
    PowerSchoolAuthError,
    PowerSchoolClient,
    _hash_password,
    map_category,
    map_status,
)

LOGIN_PAGE = """
<html><body>
<form id="LoginForm" action="/guardian/home.html" method="post">
  <input type="hidden" name="pstoken" value="tok123">
  <input type="hidden" name="contextData" value="deadbeef">
  <input type="text" name="account" value="">
  <input type="password" name="pw" value="">
</form>
</body></html>
"""

HOME_PAGE_AUTHENTICATED = """
<html><body>
<table>
<tr id="ccid_555">
  <td>1</td>
  <td><a title="Ms. Rivera">Algebra II</a></td>
  <td><a href="/guardian/scores.html?frn=555">A- (91%)</a></td>
</tr>
</table>
</body></html>
"""

ASSIGNMENTS_PAGE = """
<html><body>
<table id="assignmentsTable">
  <tr><th>Due Date</th><th>Category</th><th>Assignment</th><th>Score</th><th>%</th></tr>
  <tr><td>01/12/2026</td><td>Quiz</td><td>Chapter 4 Quiz</td><td>8/10</td><td>80%</td></tr>
  <tr><td>01/15/2026</td><td>Homework</td><td>Worksheet 4B</td><td>Missing</td><td></td></tr>
</table>
</body></html>
"""


def _handler_factory(*, valid_password: str):
    def _is_authenticated(request: httpx.Request) -> bool:
        return "sessionid=abc123" in request.headers.get("cookie", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guardian/home.html" and request.method == "GET":
            if _is_authenticated(request):
                return httpx.Response(200, text=HOME_PAGE_AUTHENTICATED)
            return httpx.Response(200, text=LOGIN_PAGE)
        if request.url.path == "/guardian/home.html" and request.method == "POST":
            from urllib.parse import unquote_plus
            form = dict(x.split("=", 1) for x in request.content.decode().split("&") if "=" in x)
            dbpw = unquote_plus(form.get("dbpw", ""))
            expected = _hash_password(valid_password, "deadbeef")
            if dbpw == expected:
                return httpx.Response(
                    200, text=HOME_PAGE_AUTHENTICATED,
                    headers={"set-cookie": "sessionid=abc123; Path=/"},
                )
            return httpx.Response(200, text=LOGIN_PAGE)  # login page re-rendered on failure
        if request.url.path == "/guardian/scores.html":
            return httpx.Response(200, text=ASSIGNMENTS_PAGE)
        return httpx.Response(404)

    return handler


def test_login_success_and_scrape():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password="correct-horse"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "student1", "correct-horse", transport=transport
        )
        try:
            await client.login()
            classes = await client.fetch_classes()
            assert len(classes) == 1
            cls = classes[0]
            assert cls.ccid == "555"
            assert cls.name == "Algebra II"
            assert cls.teacher == "Ms. Rivera"
            assert cls.grade_letter == "A-"
            assert cls.grade_percent == 91.0
            assert cls.detail_href == "/guardian/scores.html?frn=555"

            assignments = await client.fetch_assignments(cls.detail_href)
            assert len(assignments) == 2
            quiz, hw = assignments
            assert quiz.name == "Chapter 4 Quiz"
            assert map_category(quiz.category) == "quiz"
            assert quiz.score == 8.0 and quiz.points_possible == 10.0
            assert quiz.due_date == "2026-01-12"
            assert map_status(quiz) == "graded"

            assert hw.name == "Worksheet 4B"
            assert hw.is_missing is True
            assert map_status(hw) == "missing"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_probe_login_page_reports_form_found():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password="correct-horse"))
        client = PowerSchoolClient("https://fake.powerschool.com", "", "", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["has_login_form"] is True
            assert result["status_code"] == 200
            assert any("contextData" in f["input_names"] for f in result["forms"])
        finally:
            await client.aclose()

    asyncio.run(run())


def test_probe_login_page_reports_sso_redirect():
    """Simulates a district that requires SSO — no username/password form,
    so `has_login_form` should come back False instead of raising."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><head><title>Sign in with Google</title></head><body>"
                                          "<a href='https://accounts.google.com/...'>Sign in with Google</a>"
                                          "</body></html>")

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient("https://fake.powerschool.com", "", "", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["has_login_form"] is False
            assert result["page_title"] == "Sign in with Google"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_verify_session_with_valid_cookie():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password="correct-horse"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", session_cookie="sessionid=abc123", transport=transport
        )
        try:
            await client.verify_session()  # should not raise
            classes = await client.fetch_classes()
            assert len(classes) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_verify_session_with_expired_cookie_raises():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password="correct-horse"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", session_cookie="sessionid=stale", transport=transport
        )
        try:
            with pytest.raises(PowerSchoolAuthError):
                await client.verify_session()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_wrong_password_raises():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password="correct-horse"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "student1", "wrong-password", transport=transport
        )
        try:
            with pytest.raises(PowerSchoolAuthError):
                await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_crypto_round_trip(monkeypatch):
    from cryptography.fernet import Fernet
    from app.config import settings
    import app.core.crypto as crypto_module

    monkeypatch.setattr(settings, "atlas_secret_key", Fernet.generate_key().decode())
    crypto_module._fernet.cache_clear()
    token = encrypt_json({"username": "u", "password": "p"})
    assert decrypt_json(token) == {"username": "u", "password": "p"}
    crypto_module._fernet.cache_clear()


def test_crypto_requires_key(monkeypatch):
    from app.config import settings
    import app.core.crypto as crypto_module

    monkeypatch.setattr(settings, "atlas_secret_key", "")
    crypto_module._fernet.cache_clear()
    with pytest.raises(CryptoError):
        encrypt_json({"a": 1})
    crypto_module._fernet.cache_clear()
