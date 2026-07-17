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

# A district using PowerSchool's newer CAS-based login (no contextData field
# at all — credentialType/pcasServerUrl/serviceTicket instead, submitted via
# a doPCASLogin() JS handler this client doesn't implement).
CAS_LOGIN_PAGE = """
<html><head><title>Parent Sign In</title></head><body>
<form action="/guardian/home.html" method="post" name="LoginForm" id="LoginForm" onsubmit="doPCASLogin(this);">
  <input type="hidden" name="dbpw" value="" />
  <input type="hidden" name="returnUrl" value=""/>
  <input type="hidden" name="serviceName" value="PS Parent Portal"/>
  <input type="hidden" name="serviceTicket" value=""/>
  <input type="hidden" name="pcasServerUrl" value="/"/>
  <input type="hidden" name="credentialType" value="User Id and Password Credential"/>
</form>
</body></html>
"""

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

# The current PowerSchool sign-in page (as served by e.g. lexington1.powerschool.com):
# it carries the CAS ticket markers *and* a real username/password form. Its
# client-side doPCASLogin() just copies the plaintext password into `dbpw` and
# posts — no contextData hash. There is deliberately no `contextData` field here.
PCAS_LOGIN_PAGE = """
<html><head><title>Parent Sign In</title></head><body>
<form id="LoginForm" action="/guardian/home.html" method="post">
  <input type="hidden" name="dbpw" value="">
  <input type="hidden" name="translator_username" value="">
  <input type="hidden" name="translator_password" value="">
  <input type="hidden" name="translator_ldappassword" value="">
  <input type="hidden" name="returnUrl" value="">
  <input type="hidden" name="serviceName" value="PS Parent Portal">
  <input type="hidden" name="serviceTicket" value="">
  <input type="hidden" name="pcasServerUrl" value="/">
  <input type="hidden" name="credentialType" value="User Id and Password Credential">
  <input type="text" name="account" value="">
  <input type="password" name="pw" value="">
  <input type="password" name="translatorpw" value="">
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

# A grades grid shaped like Lexington's: a full attendance block
# (Last Week / This Week) sits *before* the Course column, so the course name
# is NOT in the second cell. The course/teacher cell is the only left-aligned
# one; the teacher is a teacherinfo details link + a mailto link; the term
# grade cells are "[ i ]" placeholders (new term, no grades posted yet).
HOME_PAGE_ATTENDANCE_GRID = """
<html><body>
<table class="linkDescList grid">
<tr class="center th2"><th>Exp</th><th>Last Week</th><th>This Week</th><th>Course</th><th>Q1</th><th>Absences</th><th>Tardies</th></tr>
<tr class="center th2"><th>M</th><th>T</th><th>W</th><th>H</th><th>F</th><th>M</th><th>T</th><th>W</th><th>H</th><th>F</th></tr>
<tr class="center" id="ccid_8817372">
  <td>1-3(A-E)</td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td class="notInSession"><span class="screen_readers_only">Not available</span></td>
  <td align="left">AP Calculus AB<br>
    <a class="button mini dialogM" href="teacherinfo.html?frn=00576431&nolink=true" title="Details about Daichendt, Ana Nicoleta"><em class="ui-icon"></em></a>
    <a href="mailto:adaichendt@lexington1.net">Email Daichendt, Ana Nicoleta</a>
    <span class="display-flex"><span>- Rm:</span><span>L F207</span></span>
  </td>
  <td><a href="scores.html?frn=00437309537&fg=Q1&schoolid=3">[ i ]</a></td>
  <td>0</td>
  <td>0</td>
</tr>
</table>
</body></html>
"""

HOME_PAGE_BETWEEN_TERMS = """
<html><body>
<table>
<tr id="ccid_555">
  <td>1</td>
  <td><a title="Ms. Rivera">Algebra II</a></td>
  <td><a href="/guardian/scores.html?frn=555">A- (91%)</a></td>
</tr>
<tr id="ccid_601"><td>2</td><td>Not Available</td><td></td></tr>
<tr id="ccid_602"><td>3</td><td>Not Available</td><td></td></tr>
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


# Some districts (e.g. the one that surfaced this) serve their login form
# from the combined Parent+Student tabbed page at /public/home.html, with a
# form action *relative* to that page — not from /guardian/home.html at all.
PUBLIC_LOGIN_PAGE = """
<html><body>
<div>Parent Sign In</div>
<form id="LoginForm" action="home.html" method="post">
  <input type="hidden" name="pstoken" value="tok456">
  <input type="hidden" name="contextData" value="beadfeed">
  <input type="text" name="account" value="">
  <input type="password" name="pw" value="">
</form>
<div>Student Sign In (separate SSO button, no form)</div>
</body></html>
"""


def _public_login_handler_factory(*, valid_password: str):
    def _is_authenticated(request: httpx.Request) -> bool:
        return "sessionid=xyz789" in request.headers.get("cookie", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html" and request.method == "GET":
            return httpx.Response(200, text=PUBLIC_LOGIN_PAGE)
        if request.url.path == "/public/home.html" and request.method == "POST":
            from urllib.parse import unquote_plus
            form = dict(x.split("=", 1) for x in request.content.decode().split("&") if "=" in x)
            dbpw = unquote_plus(form.get("dbpw", ""))
            expected = _hash_password(valid_password, "beadfeed")
            if dbpw == expected:
                return httpx.Response(
                    200, text=HOME_PAGE_AUTHENTICATED,
                    headers={"set-cookie": "sessionid=xyz789; Path=/"},
                )
            return httpx.Response(200, text=PUBLIC_LOGIN_PAGE)
        if request.url.path == "/guardian/home.html" and request.method == "GET":
            if _is_authenticated(request):
                return httpx.Response(200, text=HOME_PAGE_AUTHENTICATED)
            # No login form here when unauthenticated — the real one is at
            # /public/home.html. This is exactly what tripped up the probe.
            return httpx.Response(200, text="<html><body>Choose a sign-in method</body></html>")
        if request.url.path == "/guardian/scores.html":
            return httpx.Response(200, text=ASSIGNMENTS_PAGE)
        return httpx.Response(404)

    return handler


def test_login_falls_back_to_public_home_html():
    """Covers the real-world case where /guardian/home.html has no login
    form and the actual Parent Sign In form lives at /public/home.html with
    a relative form action."""
    async def run():
        transport = httpx.MockTransport(_public_login_handler_factory(valid_password="parent-pw"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "parentuser", "parent-pw", transport=transport
        )
        try:
            await client.login()
            classes = await client.fetch_classes()
            assert len(classes) == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def _pcas_login_handler_factory(*, valid_password: str):
    """Fake portal that behaves like the current PowerSchool sign-in page:
    the login form has no contextData and expects `dbpw` to be the plaintext
    password (what doPCASLogin copies in), not an HMAC hash."""
    def _is_authenticated(request: httpx.Request) -> bool:
        return "sessionid=pcas1" in request.headers.get("cookie", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/public/home.html", "/guardian/home.html") and request.method == "GET":
            if _is_authenticated(request):
                return httpx.Response(200, text=HOME_PAGE_AUTHENTICATED)
            return httpx.Response(200, text=PCAS_LOGIN_PAGE)
        if request.url.path == "/guardian/home.html" and request.method == "POST":
            from urllib.parse import unquote_plus
            form = {k: unquote_plus(v) for k, v in
                    (x.split("=", 1) for x in request.content.decode().split("&") if "=" in x)}
            if form.get("account") == "parentuser" and form.get("dbpw") == valid_password \
                    and form.get("pw") == valid_password:
                return httpx.Response(
                    200, text=HOME_PAGE_AUTHENTICATED,
                    headers={"set-cookie": "sessionid=pcas1; Path=/"},
                )
            return httpx.Response(200, text=PCAS_LOGIN_PAGE)  # re-render on failure
        if request.url.path == "/guardian/scores.html":
            return httpx.Response(200, text=ASSIGNMENTS_PAGE)
        return httpx.Response(404)

    return handler


def test_pcas_login_posts_plaintext_dbpw_and_scrapes():
    """The modern PowerSchool sign-in page (CAS ticket markers + a real
    username/password form, no contextData). Login must post the plaintext
    password as `dbpw` and land on the grades page."""
    async def run():
        transport = httpx.MockTransport(_pcas_login_handler_factory(valid_password="parent-pw"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "parentuser", "parent-pw", transport=transport
        )
        try:
            await client.login()  # should not raise
            classes = await client.fetch_classes()
            assert len(classes) == 1
            assert classes[0].name == "Algebra II"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_pcas_login_wrong_password_raises():
    async def run():
        transport = httpx.MockTransport(_pcas_login_handler_factory(valid_password="parent-pw"))
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "parentuser", "wrong-pw", transport=transport
        )
        try:
            with pytest.raises(PowerSchoolAuthError):
                await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_pcas_form_classified_as_automatable():
    """A form with CAS ticket markers but a real password field is the
    automatable 'pcas' flow — not the unsupported ticket/SSO 'cas' one."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html":
            return httpx.Response(200, text=PCAS_LOGIN_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient("https://fake.powerschool.com", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["has_login_form"] is True
            assert result["login_type"] == "pcas"
        finally:
            await client.aclose()

    asyncio.run(run())


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


def test_fetch_classes_skips_not_available_placeholders():
    """Between school years/terms, PowerSchool lists requested-but-unscheduled
    courses as "Not Available" instead of a real section — these aren't
    classes the student is actually taking and shouldn't be imported."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guardian/home.html" and request.method == "GET":
            return httpx.Response(200, text=HOME_PAGE_BETWEEN_TERMS)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient(
            "https://fake.powerschool.com", session_cookie="sessionid=abc123", transport=transport
        )
        try:
            classes = await client.fetch_classes()
            assert len(classes) == 1
            assert classes[0].name == "Algebra II"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_fetch_classes_attendance_grid_layout():
    """Districts with a Last-Week/This-Week attendance block push the course
    name well past the second column. The course cell is the left-aligned one;
    the name, teacher (from the teacherinfo/mailto links) and period must all
    come out right, and the "[ i ]" term-grade placeholders must yield no
    grade rather than a false letter from the course title or room number."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guardian/home.html" and request.method == "GET":
            return httpx.Response(200, text=HOME_PAGE_ATTENDANCE_GRID)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient(
            "https://fake.powerschool.com", session_cookie="sessionid=abc123", transport=transport
        )
        try:
            classes = await client.fetch_classes()
            assert len(classes) == 1
            cls = classes[0]
            assert cls.name == "AP Calculus AB"
            assert cls.teacher == "Daichendt, Ana Nicoleta"
            assert cls.period == "1-3(A-E)"
            assert cls.grade_letter is None and cls.grade_percent is None
            assert cls.detail_href == "scores.html?frn=00437309537&fg=Q1&schoolid=3"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_debug_home_page_reports_raw_rows():
    """Diagnostic used when a district's table layout doesn't match this
    client's cell-index assumptions — reports the header row and a few
    sample course rows verbatim so the real structure can be inspected."""
    WIDE_HOME_PAGE = """
    <html><body>
    <table>
      <tr><th>Exp</th><th>Last Week</th><th>Course</th><th>Grade</th></tr>
      <tr id="ccid_555">
        <td>1-3(A-E)</td>
        <td>Not Available</td>
        <td><a title="Ms. Rivera">Algebra II</a></td>
        <td>[i]</td>
      </tr>
    </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/guardian/home.html" and request.method == "GET":
            return httpx.Response(200, text=WIDE_HOME_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient(
            "https://fake.powerschool.com", session_cookie="sessionid=abc123", transport=transport
        )
        try:
            result = await client.debug_home_page()
            assert result["ccid_row_count"] == 1
            assert "Exp" in result["header_row_html"]
            assert "ccid_555" in result["sample_row_html"][0]
            assert "Algebra II" in result["sample_row_html"][0]
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


def test_probe_detects_cas_login_form():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html":
            return httpx.Response(200, text=CAS_LOGIN_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient("https://fake.powerschool.com", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["has_login_form"] is True
            assert result["login_type"] == "cas"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_raises_clear_error_for_cas_form():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html":
            return httpx.Response(200, text=CAS_LOGIN_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient(
            "https://fake.powerschool.com", "parentuser", "parent-pw", transport=transport
        )
        try:
            with pytest.raises(PowerSchoolAuthError, match="CAS"):
                await client.login()
        finally:
            await client.aclose()

    asyncio.run(run())


def test_probe_reports_browser_fallback_available_by_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "vercel", "")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html":
            return httpx.Response(200, text=CAS_LOGIN_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient("https://fake.powerschool.com", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["browser_fallback_available"] is True
        finally:
            await client.aclose()

    asyncio.run(run())


def test_probe_reports_no_browser_fallback_on_serverless(monkeypatch):
    """On Vercel (settings.vercel set), there's no Chromium binary and no
    room for a long-running browser automation — the probe should say so
    up front instead of only failing once the user tries to connect."""
    from app.config import settings

    monkeypatch.setattr(settings, "vercel", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/public/home.html":
            return httpx.Response(200, text=CAS_LOGIN_PAGE)
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = PowerSchoolClient("https://fake.powerschool.com", transport=transport)
        try:
            result = await client.probe_login_page()
            assert result["browser_fallback_available"] is False
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
