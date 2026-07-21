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

from app.integrations.schoology_scraper import (
    SchoologyScraperAuthError,
    SchoologyScraperClient,
    parse_materials_page,
)

BASE_URL = "https://lexington1.schoology.com"
VALID_USER = "student@example.com"
VALID_PASS = "correct-horse-battery-staple"

# Verbatim link dump from a real course materials page (AP Biology: Section
# 2, course id 8435659601) — confirms the boilerplate filter against actual
# Schoology chrome rather than a guess at its shape. The only real content on
# this page is the one folder, which happens to appear twice (top nav +
# sidebar) — both must collapse to a single parsed entry.
REAL_MATERIALS_PAGE_LINKS = [
    {"text": "Skip to Content", "href": "#main"},
    {"text": "Folder. Syllabus and Other Important Documents",
     "href": "/course/8435659601/materials?f=1003379126"},
    {"text": "Course Profile", "href": "/course/8435659601"},
    {"text": "Current Menu Item Materials Dropdown Materials", "href": "/course/8435659601/materials"},
    {"text": "Updates", "href": "/course/8435659601/updates"},
    {"text": "Grades", "href": "/course/8435659601/student_grades"},
    {"text": "Mastery", "href": "/course/8435659601/student_mastery"},
    {"text": "Members", "href": "/course/8435659601/members"},
    {"text": "Course Profile", "href": "/course-templates/8435659601"},
    {"text": "Canva for Education", "href": "/apps/4673660618/run/course/8435659601"},
    {"text": "CodeAI", "href": "/apps/7293540612/run/course/8435659601"},
    {"text": "Google Meet", "href": "/apps/7169483585/run/course/8435659601"},
    {"text": "Lingco Classroom", "href": "/apps/2414152280/run/course/8435659601"},
    {"text": "Math Nation", "href": "/apps/2940901321/run/course/8435659601"},
    {"text": "McGraw Hill K-12 SSO", "href": "/apps/652250061/run/course/8435659601"},
    {"text": "Nearpod LTI 1.3 by Nearpod", "href": "/apps/6695120929/run/course/8435659601"},
    {"text": "Newsela", "href": "/apps/2147537916/run/course/8435659601"},
    {"text": "AP Biology: Section 2", "href": "/course/8435659601"},
    {"text": "Lexington High School", "href": "/school/8896103"},
    {"text": "All Materials", "href": "/course/8435659601/materials"},
    {"text": "Assignments", "href": "/course/8435659601/materials?list_filter=assignments"},
    {"text": "Tests/Quizzes", "href": "/course/8435659601/materials?list_filter=assessments"},
    {"text": "Files", "href": "/course/8435659601/materials?list_filter=documents_files"},
    {"text": "Links", "href": "/course/8435659601/materials?list_filter=documents_links"},
    {"text": "Discussions", "href": "/course/8435659601/materials?list_filter=discussion"},
    {"text": "Pages", "href": "/course/8435659601/materials?list_filter=pages"},
    {"text": "Albums", "href": "/course/8435659601/materials?list_filter=album"},
    {"text": "SCORM", "href": "/course/8435659601/materials?list_filter=scorm"},
    {"text": "Web Content", "href": "/course/8435659601/materials?list_filter=web"},
    {"text": "External Tools", "href": "/course/8435659601/materials?list_filter=documents_external_tools"},
    {"text": "Assessments", "href": "/course/8435659601/materials?list_filter=course_assessment"},
    {"text": "Managed Assessments", "href": "/course/8435659601/materials?list_filter=common_assessments"},
    {"text": "Syllabus and Other Important Documents", "href": "/course/8435659601/materials?f=1003379126"},
    {"text": "Export", "href": "/calendar/feed/export/course/8435659601"},
    {"text": "1 Admin", "href": "/course/8435659601/members"},
    {"text": "", "href": "/user/131510895"},
]


def test_parse_materials_page_filters_real_boilerplate_dump():
    """The only two non-chrome things on this real page are the one folder
    link and its duplicate — both collapse to the single classified folder."""
    parsed = parse_materials_page(REAL_MATERIALS_PAGE_LINKS)
    assert len(parsed) == 1
    item = parsed[0]
    assert item.name == "Syllabus and Other Important Documents"
    assert item.kind == "folder"
    assert item.material_type == "Folder"
    assert item.href == "/course/8435659601/materials?f=1003379126"


def test_parse_materials_page_classifies_items_vs_folders():
    links = [
        {"text": "Folder. Unit 1", "href": "/course/1/materials?f=10"},
        {"text": "File. Lab handout.pdf", "href": "/attachment/download/123"},
        {"text": "Link. Khan Academy video", "href": "/materials/link/456"},
        {"text": "Some plain title with no type prefix", "href": "/materials/page/789"},
    ]
    parsed = parse_materials_page(links)
    by_name = {p.name: p for p in parsed}
    assert by_name["Unit 1"].kind == "folder"
    assert by_name["Lab handout.pdf"].kind == "item"
    assert by_name["Lab handout.pdf"].material_type == "File"
    assert by_name["Khan Academy video"].material_type == "Link"
    # No recognized type prefix -> still comes through as a plain item,
    # never silently dropped.
    assert by_name["Some plain title with no type prefix"].kind == "item"
    assert by_name["Some plain title with no type prefix"].material_type == ""

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

# A parent account's own browser link for a course's home page lives on a
# completely different domain (app.schoology.com) than login (the district
# subdomain) — see schoology_scraper.py's debug_materials_page docstring.
PREVIEW_PARENT_PAGE = """
<html><head><title>AP Biology</title></head>
<body>
  <a href="/course/8435659601/materials">Materials</a>
  <a href="/course/8435659601/grades">Grades</a>
</body></html>
"""

# app.schoology.com's login form is district-agnostic — same shape, but
# school_nid arrives blank instead of pre-filled (the district subdomain
# identifies the school by itself; the shared app domain can't).
APP_LOGIN_PAGE = """
<html><body>
<form id="s-user-login-form" action="/login" method="post">
  <input type="text" name="mail" id="edit-mail" placeholder="Email or Username" />
  <input type="password" name="pass" id="edit-pass" placeholder="Password" />
  <input type="text" name="school" id="edit-school" placeholder="School or Postal Code" />
  <input type="hidden" name="school_nid" id="edit-school-nid" value="" />
  <input type="submit" name="op" id="edit-submit" value="Log in" />
  <input type="hidden" name="form_build_id" id="build-3" value="app-form-build-id-xyz789" />
  <input type="hidden" name="form_id" id="edit-s-user-login-form" value="s_user_login_form" />
</form>
</body></html>
"""

APP_DASHBOARD_PAGE = "<html><head><title>Schoology</title></head><body>Welcome!</body></html>"


def _handler_factory(*, valid_password: str, app_login_captured: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host == "app.schoology.com":
            if path == "/login" and request.method == "GET":
                return httpx.Response(200, text=APP_LOGIN_PAGE)
            if path == "/login" and request.method == "POST":
                form = dict(httpx.QueryParams(request.content.decode()))
                if app_login_captured is not None:
                    app_login_captured.update(form)
                if form.get("pass") == valid_password:
                    return httpx.Response(200, text=APP_DASHBOARD_PAGE)
                return httpx.Response(200, text=APP_LOGIN_PAGE)
            if path == "/course/8435659601/preview/23381548/parent":
                return httpx.Response(200, text=PREVIEW_PARENT_PAGE)
            return httpx.Response(404, text="not found")
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


def test_debug_materials_page_without_student_uid_only_tries_district_domain():
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password=VALID_PASS))
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.debug_materials_page("8435659601")
            assert set(result.keys()) == {"district_materials"}
            page = result["district_materials"]
            assert page["status_code"] == 200
            assert page["title"] == "Course Materials"
            hrefs = {l["href"] for l in page["links"]}
            assert "/attachment/download/123" in hrefs
            assert "/course/8435659601/materials/folder/42" in hrefs
        finally:
            await client.aclose()

    asyncio.run(run())


def test_debug_materials_page_with_student_uid_also_tries_app_domain():
    """A parent account's course-home link lives on a different domain
    (app.schoology.com/course/{id}/preview/{uid}/parent) than login, with its
    own separate session — confirmed against a real account, logging in only
    at the district subdomain got redirected to a login page when hitting
    app.schoology.com. Both candidates must be probed and reported
    independently, and the app domain needs its own login first."""
    async def run():
        transport = httpx.MockTransport(_handler_factory(valid_password=VALID_PASS))
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.debug_materials_page("8435659601", student_uid="23381548")
            assert set(result.keys()) == {"district_materials", "app_preview_parent"}
            preview = result["app_preview_parent"]
            assert preview["status_code"] == 200
            assert preview["title"] == "AP Biology"
            assert preview["requested_url"] == (
                "https://app.schoology.com/course/8435659601/preview/23381548/parent"
            )
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_app_domain_reuses_school_nid_captured_from_district_login():
    """app.schoology.com's own login form arrives with school_nid blank (it
    has no district context) — the client must supply the value it already
    captured from the district subdomain's login page instead of submitting
    it blank, or the login has nothing to identify the school with."""
    captured: dict = {}

    async def run():
        transport = httpx.MockTransport(
            _handler_factory(valid_password=VALID_PASS, app_login_captured=captured)
        )
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            await client.login()
            assert client._school_nid == "129435949"
            await client._login_app_domain()
            assert client._app_logged_in is True
        finally:
            await client.aclose()

    asyncio.run(run())
    assert captured["school_nid"] == "129435949"
    assert captured["mail"] == VALID_USER
    assert captured["form_build_id"] == "app-form-build-id-xyz789"


def test_debug_materials_page_reports_app_domain_login_failure_without_crashing():
    async def run():
        # Right password for the district subdomain, wrong for the app
        # domain — an inconsistent but real-world-plausible case (e.g. the
        # account only exists on one of the two).
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)  # always "fails"
                return httpx.Response(404)
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path.startswith("/course/") and request.url.path.endswith("/materials"):
                return httpx.Response(200, text=MATERIALS_PAGE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.debug_materials_page("8435659601", student_uid="23381548")
            assert result["district_materials"]["status_code"] == 200
            assert "app.schoology.com login failed" in result["app_preview_parent"]["error"]
        finally:
            await client.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# walk_materials: recurses real folders, filters chrome on every page it
# visits (not just the root), and dedupes by name across a re-scan.
# ---------------------------------------------------------------------------
WALK_ROOT_PAGE = """
<html><body>
<a href="#main">Skip to Content</a>
<a href="/course/8435659601/materials?f=1003379126">Folder. Syllabus and Other Important Documents</a>
</body></html>
"""

WALK_SYLLABUS_FOLDER_PAGE = """
<html><body>
<a href="#main">Skip to Content</a>
<a href="/attachment/download/500">File. Syllabus.pdf</a>
<a href="/course/8435659601/materials?f=999">Folder. Nested Unit</a>
</body></html>
"""

WALK_NESTED_FOLDER_PAGE = """
<html><body>
<a href="/materials/link/700">Link. Extra Resource</a>
</body></html>
"""


def _walk_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/login" and request.method == "GET":
        return httpx.Response(200, text=LOGIN_PAGE)
    if path == "/login" and request.method == "POST":
        form = dict(httpx.QueryParams(request.content.decode()))
        if form.get("pass") == VALID_PASS:
            return httpx.Response(200, text=DASHBOARD_PAGE)
        return httpx.Response(200, text=LOGIN_FAILED_PAGE)
    if path == "/course/8435659601/materials":
        f = request.url.params.get("f")
        if f == "1003379126":
            return httpx.Response(200, text=WALK_SYLLABUS_FOLDER_PAGE)
        if f == "999":
            return httpx.Response(200, text=WALK_NESTED_FOLDER_PAGE)
        return httpx.Response(200, text=WALK_ROOT_PAGE)
    return httpx.Response(404, text="not found")


def test_walk_materials_recurses_folders_and_returns_leaf_items():
    async def run():
        transport = httpx.MockTransport(_walk_handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            items = await client.walk_materials("8435659601")
            by_name = {i.name: i for i in items}
            assert set(by_name) == {"Syllabus.pdf", "Extra Resource"}
            assert by_name["Syllabus.pdf"].folder_path == "Syllabus and Other Important Documents"
            assert by_name["Extra Resource"].folder_path == (
                "Syllabus and Other Important Documents/Nested Unit"
            )
            # Folders themselves are never returned as items.
            assert all(i.kind == "item" for i in items)
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_materials_skips_already_known_names_on_a_rescan():
    """A rescan that already recorded "Syllabus.pdf" only needs to surface
    what's new — mirrors the "a" then "a"+"b" -> only "b" example."""
    async def run():
        transport = httpx.MockTransport(_walk_handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            items = await client.walk_materials("8435659601", known_names={"Syllabus.pdf"})
            assert {i.name for i in items} == {"Extra Resource"}
        finally:
            await client.aclose()

    asyncio.run(run())
