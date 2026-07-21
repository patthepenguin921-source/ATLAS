"""Schoology materials scraper — login + materials-page fetch, exercised
against a fake login form (httpx.MockTransport) built from the real,
unauthenticated Drupal-style login page shape confirmed against a live
district (see schoology_scraper.py's module docstring): `POST /login` with
`mail`, `pass`, `school_nid`, `op`, and Drupal's `form_build_id`/`form_id`.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.integrations.schoology_scraper import SchoologyScraperAuthError, SchoologyScraperClient

BASE_URL = "https://lexington1.schoology.com"
VALID_USER = "student@example.com"
VALID_PASS = "correct-horse-battery-staple"

LOGIN_PAGE = """
<html><body>
<form id="s-user-login-form" action="/login" method="post">
  <input type="text" name="mail" id="edit-mail" placeholder="Email or Username" />
  <input type="password" name="pass" id="edit-pass" placeholder="Password" />
  <input type="hidden" name="school_nid" id="edit-school-nid" value="129435949" />
  <input type="submit" name="op" id="edit-submit" value="Log in" />
  <input type="hidden" name="form_build_id" id="build-1" value="form-build-id-abc123" />
  <input type="hidden" name="form_id" id="edit-s-user-login-form" value="s_user_login_form" />
</form>
</body></html>
"""

DASHBOARD_PAGE = "<html><head><title>Home</title></head><body>Welcome back!</body></html>"

LOGIN_FAILED_PAGE = """
<html><body>
<div class="messages error">Incorrect username or password.</div>
<form id="s-user-login-form" action="/login" method="post">
  <input type="text" name="mail" id="edit-mail" />
  <input type="password" name="pass" id="edit-pass" />
  <input type="hidden" name="school_nid" id="edit-school-nid" value="129435949" />
  <input type="submit" name="op" id="edit-submit" value="Log in" />
  <input type="hidden" name="form_build_id" id="build-2" value="form-build-id-def456" />
  <input type="hidden" name="form_id" id="edit-s-user-login-form" value="s_user_login_form" />
</form>
</body></html>
"""

MATERIALS_PAGE = """
<html><head><title>Course Materials</title></head>
<body>
  <div id="materials">
    <a href="/course/8435659601/materials/folder/42">Unit 1</a>
    <a href="/attachment/download/123">Lab handout.pdf</a>
  </div>
</body></html>
"""


def _handler_factory(*, valid_password: str):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login" and request.method == "GET":
            return httpx.Response(200, text=LOGIN_PAGE)
        if path == "/login" and request.method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            if form.get("pass") == valid_password:
                return httpx.Response(200, text=DASHBOARD_PAGE)
            return httpx.Response(200, text=LOGIN_FAILED_PAGE)
        if path.startswith("/course/") and path.endswith("/materials"):
            return httpx.Response(200, text=MATERIALS_PAGE)
        return httpx.Response(404, text="not found")

    return handler


def test_login_success_with_valid_credentials():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password=VALID_PASS))
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            await client.login()
            assert client._logged_in is True
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_failure_with_wrong_password_raises():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password=VALID_PASS))
        client = SchoologyScraperClient(BASE_URL, VALID_USER, "wrong-password", transport=transport)
        try:
            with pytest.raises(SchoologyScraperAuthError, match="check your username and password"):
                await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_posts_the_real_form_fields_back():
    """Regression: the real login page's form_build_id/form_id/school_nid
    must be echoed back verbatim, not hardcoded — a wrong form_build_id is
    exactly what a failed login looks like from Schoology's side."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login" and request.method == "GET":
            return httpx.Response(200, text=LOGIN_PAGE)
        if request.url.path == "/login" and request.method == "POST":
            captured.update(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, text=DASHBOARD_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())
    assert captured["mail"] == VALID_USER
    assert captured["pass"] == VALID_PASS
    assert captured["school_nid"] == "129435949"
    assert captured["form_build_id"] == "form-build-id-abc123"
    assert captured["form_id"] == "s_user_login_form"
    assert captured["op"] == "Log in"


def test_login_with_no_recognizable_form_raises():
    """An SSO-only district (no username/password form at all) must fail
    with a clear, distinguishable message rather than a confusing crash."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(200, text="<html><body>Redirecting to SSO…</body></html>")
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            with pytest.raises(SchoologyScraperAuthError, match="SSO"):
                await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_debug_materials_page_logs_in_first_and_reports_links():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password=VALID_PASS))
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.debug_materials_page("8435659601")
            assert result["status_code"] == 200
            assert result["title"] == "Course Materials"
            hrefs = {l["href"] for l in result["links"]}
            assert "/attachment/download/123" in hrefs
            assert "/course/8435659601/materials/folder/42" in hrefs
        finally:
            await client.aclose()

    asyncio.run(run())
