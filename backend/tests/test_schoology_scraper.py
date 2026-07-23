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


def test_parse_materials_page_classifies_folder_by_href_without_type_prefix():
    """A district template can render the folder link without the "Folder. "
    screen-reader prefix landing in the link text. The folder href shape
    (`?f=<id>` / `/materials/folder/<id>`) must still classify it as a folder
    so it gets recursed into — otherwise it looks like a leaf item and its
    contents are never opened (the reported symptom)."""
    links = [
        {"text": "Syllabus and Other Important Documents",
         "href": "/course/8435659601/materials?f=1003379126"},
        {"text": "Unit 1 Resources", "href": "/course/8435659601/materials/folder/55"},
    ]
    parsed = parse_materials_page(links)
    by_name = {p.name: p for p in parsed}
    assert by_name["Syllabus and Other Important Documents"].kind == "folder"
    assert by_name["Unit 1 Resources"].kind == "folder"


def test_parse_materials_page_classifies_file_by_href_without_type_prefix():
    """A bare document link (no "File. " prefix) whose href points at an
    attachment/document must be tagged a File so the caller downloads it
    rather than filing a plain reference."""
    links = [
        {"text": "Lab handout", "href": "/attachment/12345/download"},
        {"text": "Reading", "href": "/course/8435659601/materials/document/678"},
        {"text": "Khan Academy", "href": "https://khanacademy.org/x"},
    ]
    by_name = {p.name: p for p in parse_materials_page(links)}
    assert by_name["Lab handout"].material_type == "File"
    assert by_name["Reading"].material_type == "File"
    # A genuine external link is left as a plain item, never mis-tagged File.
    assert by_name["Khan Academy"].material_type == ""


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


def test_login_with_no_recognizable_form_includes_diagnostic_details():
    """The "no login form" error must include what was actually seen (HTTP
    status, final URL, page title) — not just the generic "may enforce SSO"
    guess. A plain guess turned out to be a dead end in practice: the same
    URL fetched with no session at all can come back with a perfectly
    normal login form, so the real cause of a specific failure has to come
    from what that specific request actually got back, not an assumption."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(
                503, text="<html><head><title>Access Denied</title></head><body>blocked</body></html>",
            )
        return httpx.Response(404)

    async def run():
        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            with pytest.raises(SchoologyScraperAuthError) as exc_info:
                await client.login()
            message = str(exc_info.value)
            assert "503" in message
            assert "Access Denied" in message
            assert BASE_URL in message
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


# ---------------------------------------------------------------------------
# walk_materials(student_uid=...) — a parent account's real materials can
# live only on the app.schoology.com preview URL, not the district
# subdomain (see debug_materials_page's docstring); student_uid must make
# walk_materials check both, merging whatever each one finds.
# ---------------------------------------------------------------------------
APP_PREVIEW_MATERIALS_PAGE = """
<html><body>
<a href="/materials/link/900">Link. Extra Credit Article</a>
</body></html>
"""


def _walk_handler_with_app_domain(request: httpx.Request) -> httpx.Response:
    if request.url.host == "app.schoology.com":
        if request.url.path == "/login" and request.method == "GET":
            return httpx.Response(200, text=APP_LOGIN_PAGE)
        if request.url.path == "/login" and request.method == "POST":
            return httpx.Response(200, text=APP_DASHBOARD_PAGE)
        if request.url.path == "/course/8435659601/preview/23381548/parent":
            return httpx.Response(200, text=APP_PREVIEW_MATERIALS_PAGE)
        return httpx.Response(404, text="not found")
    return _walk_handler(request)


def test_walk_materials_with_student_uid_also_walks_app_domain_preview():
    async def run():
        transport = httpx.MockTransport(_walk_handler_with_app_domain)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            items = await client.walk_materials("8435659601", student_uid="23381548")
            names = {i.name for i in items}
            # Both the district subdomain's own materials and the app-domain
            # preview's materials show up, merged into one result.
            assert "Syllabus.pdf" in names
            assert "Extra Credit Article" in names
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_materials_without_student_uid_never_hits_parent_preview():
    """Without a student uid there's no parent to preview, so the per-student
    parent-view URL must never be requested. The app-domain *materials* page
    may still be probed (courses can live on app.schoology.com even for a
    non-parent account), so that's no longer forbidden — only the
    `/preview/{uid}/parent` shape is."""
    async def run():
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(str(request.url))
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_DASHBOARD_PAGE)
                return httpx.Response(404)
            return _walk_handler(request)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            await client.walk_materials("8435659601")
            assert not any("/preview/" in u for u in requested_paths)
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_known_url_only_requests_the_given_url():
    """Unlike `walk_materials` (which tries several candidate shapes),
    `walk_known_url` must never guess — only the exact URL it's handed (and
    whatever folders it links to) gets requested, never the district
    materials page or the app-domain plain /materials page."""
    async def run():
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(str(request.url))
            return _walk_handler_with_app_domain(request)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            items = await client.walk_known_url(
                "https://app.schoology.com/course/8435659601/preview/23381548/parent"
            )
            assert {i.name for i in items} == {"Extra Credit Article"}
            materials_requests = {
                u for u in requested_paths
                if "/course/8435659601/materials" in u or "/preview/" in u
            }
            assert materials_requests == {
                "https://app.schoology.com/course/8435659601/preview/23381548/parent"
            }
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_known_url_survives_app_domain_login_failure():
    """A failed app.schoology.com login (e.g. it's temporarily serving a
    challenge page instead of the login form) must not raise out of
    `walk_known_url` — the caller's own district-subdomain login already
    succeeded, so this should report an empty result via `trace`, the same
    way `walk_materials`/`debug_materials_page` treat that failure as
    non-fatal. Regression: this used to propagate and crash the entire
    debug-walk-materials request for every course in the batch, not just
    the one whose materials happen to live on the app domain."""
    async def run():
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
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            trace: list[dict] = []
            items = await client.walk_known_url(
                "https://app.schoology.com/course/8435659601/preview/23381548/parent",
                trace=trace,
            )
            assert items == []
            assert trace and "error" in trace[0]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_login_app_domain_failure_is_cached_not_retried():
    """A real Schoology login failure (rate-limited, challenge-gated, …)
    must only be attempted once per client lifetime, not retried on every
    course a debug/sync run probes — a single run walking several known
    courses (each calling walk_known_url, each calling _login_app_domain)
    used to re-POST the login for every single one of them, compounding the
    very repeated-automated-login pattern that trips a site's own bot
    detection in the first place."""
    async def run():
        app_login_post_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal app_login_post_count
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    app_login_post_count += 1
                    return httpx.Response(200, text=APP_LOGIN_PAGE)  # always "fails"
                return httpx.Response(404)
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            # Three "courses" in one client lifetime, same as one debug/sync
            # run walking three known courses in a row.
            for _ in range(3):
                items = await client.walk_known_url(
                    "https://app.schoology.com/course/8435659601/preview/23381548/parent",
                )
                assert items == []
            assert app_login_post_count == 1
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_materials_with_student_uid_survives_app_domain_login_failure():
    """The district-subdomain results must still stand even if the
    app-domain login fails for some reason (e.g. the account only exists on
    one of the two)."""
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)  # always "fails"
                return httpx.Response(404)
            return _walk_handler(request)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            items = await client.walk_materials("8435659601", student_uid="23381548")
            assert {i.name for i in items} == {"Syllabus.pdf", "Extra Resource"}
        finally:
            await client.aclose()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# download_file — the download must confirm the response is an actual file
# (by content-type) rather than trusting Schoology's own "File." label,
# since an unconfirmed href could just as easily lead to an intermediate
# HTML detail page instead of a direct download.
# ---------------------------------------------------------------------------
def test_download_file_returns_content_for_a_real_file():
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path == "/attachment/download/1":
                return httpx.Response(
                    200, content=b"%PDF-1.4 fake bytes", headers={"content-type": "application/pdf"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.download_file(f"{BASE_URL}/attachment/download/1")
            assert result is not None
            content, content_type = result
            assert content == b"%PDF-1.4 fake bytes"
            assert content_type == "application/pdf"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_download_file_returns_none_for_an_html_detail_page():
    """A href labeled "File." by Schoology but that actually leads to an
    intermediate HTML detail page (not a direct download) must not be
    mistaken for the document itself."""
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path == "/materials/file-detail/1":
                return httpx.Response(
                    200, text="<html><body>Some detail page</body></html>",
                    headers={"content-type": "text/html; charset=utf-8"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.download_file(f"{BASE_URL}/materials/file-detail/1")
            assert result is None
        finally:
            await client.aclose()

    asyncio.run(run())


HOME_COURSE_LIST_PAGE = """
<html><body>
  <nav>
    <a href="/course/8435659601">AP Biology: Section 2</a>
    <a href="/course/8435659601/updates">Updates</a>
    <a href="/course/7654321000">AP US History</a>
    <a href="/user/131510895"></a>
    <a href="/course/9999999999"></a>  <!-- icon link, no text: skipped -->
  </nav>
</body></html>
"""


def test_list_courses_discovers_enrolled_sections_login_only():
    """A login-only account (no API key) must still be able to discover its
    courses — section id + name — straight from the web UI, or nothing can
    be synced/probed. Only the exact /course/{id} profile link supplies the
    name; sub-path nav links and text-less icon links are ignored."""
    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path == "/home":
                return httpx.Response(200, text=HOME_COURSE_LIST_PAGE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            courses = await client.list_courses()
            by_id = {c["id"]: c["name"] for c in courses}
            assert by_id == {
                "8435659601": "AP Biology: Section 2",
                "7654321000": "AP US History",
            }
        finally:
            await client.aclose()

    asyncio.run(run())


def test_list_courses_also_discovers_parent_courses_on_app_domain():
    """A parent account's courses can live only on app.schoology.com — the
    course list must merge what both domains expose."""
    app_home = """
    <html><body><a href="/course/5550001">Parent-only Course</a></body></html>
    """

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_DASHBOARD_PAGE)
                if request.url.path == "/home":
                    return httpx.Response(200, text=app_home)
                return httpx.Response(404)
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path == "/home":
                return httpx.Response(200, text=HOME_COURSE_LIST_PAGE)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            by_id = {c["id"]: c["name"] for c in await client.list_courses()}
            assert by_id["8435659601"] == "AP Biology: Section 2"  # district
            assert by_id["5550001"] == "Parent-only Course"  # app domain
        finally:
            await client.aclose()

    asyncio.run(run())


def test_list_courses_discovers_courses_from_parent_home():
    """A parent account's own login lands on /parent/home (confirmed
    against a real account) — not the plain /home a student account lands
    on. Without probing /parent/home too, a parent login had nothing to
    crawl on the app domain even when /home and /courses came back empty."""
    parent_home = """
    <html><body><a href="/course/8435659601/preview/23381548/parent">AP Biology: Section 2</a></body></html>
    """

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_DASHBOARD_PAGE)
                if request.url.path == "/parent/home":
                    return httpx.Response(200, text=parent_home)
                return httpx.Response(404)
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            return httpx.Response(404)  # /home, /courses etc. come back empty

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            courses = await client.list_courses()
            by_id = {c["id"]: c for c in courses}
            assert by_id["8435659601"]["name"] == "AP Biology: Section 2"
            assert by_id["8435659601"]["student_uid"] == "23381548"
        finally:
            await client.aclose()

    asyncio.run(run())


def test_list_courses_extracts_student_uid_from_parent_preview_links():
    """A parent account's course links are per-student preview URLs
    (/course/{id}/preview/{student_uid}/parent). list_courses must pull the
    student uid out so the materials walk can reach the parent-view pages."""
    parent_home = """
    <html><body>
      <a href="/course/8435659601/preview/23381548/parent">AP Biology: Section 2</a>
      <a href="/course/8435650700/materials">DE Entrprnrshp: Section 901</a>
    </body></html>
    """

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "app.schoology.com":
                if request.url.path == "/login" and request.method == "GET":
                    return httpx.Response(200, text=APP_LOGIN_PAGE)
                if request.url.path == "/login" and request.method == "POST":
                    return httpx.Response(200, text=APP_DASHBOARD_PAGE)
                if request.url.path == "/home":
                    return httpx.Response(200, text=parent_home)
                return httpx.Response(404)
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            return httpx.Response(404)  # district has no course list here

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            courses = await client.list_courses()
            by_id = {c["id"]: c for c in courses}
            assert by_id["8435659601"]["name"] == "AP Biology: Section 2"
            assert by_id["8435659601"]["student_uid"] == "23381548"
            # A direct /materials course (no preview) carries no student uid.
            assert by_id["8435650700"]["name"] == "DE Entrprnrshp: Section 901"
            assert by_id["8435650700"]["student_uid"] is None
        finally:
            await client.aclose()

    asyncio.run(run())


def test_walk_materials_trace_records_each_visited_page():
    """The optional trace must explain an otherwise-blank walk: each visited
    page's url, status, link counts, and whether it hit a login wall."""
    async def run():
        transport = httpx.MockTransport(_walk_handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            trace: list[dict] = []
            await client.walk_materials("8435659601", trace=trace)
            assert trace, "trace should not be empty"
            root = trace[0]
            assert root["requested_url"].endswith("/course/8435659601/materials")
            assert root["status_code"] == 200
            assert root["looks_like_login"] is False
            assert "Syllabus and Other Important Documents" in root["folders"]
        finally:
            await client.aclose()

    asyncio.run(run())


def test_download_file_follows_detail_page_to_real_attachment():
    """The common Schoology case: a material link opens an HTML viewer/detail
    page that embeds the real file, rather than downloading it directly. The
    detail page's download link must be followed through to the actual file
    instead of giving up at the first HTML response (the reported "never
    downloaded the documents" symptom)."""
    detail_html = """
    <html><body>
      <h1>Lab handout.pdf</h1>
      <a class="download-button" href="/attachment/9001/download">Download</a>
      <iframe src="/viewer?doc=9001"></iframe>
    </body></html>
    """

    async def run():
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login" and request.method == "GET":
                return httpx.Response(200, text=LOGIN_PAGE)
            if request.url.path == "/login" and request.method == "POST":
                return httpx.Response(200, text=DASHBOARD_PAGE)
            if request.url.path == "/course/1/materials/document/9001":
                return httpx.Response(
                    200, text=detail_html, headers={"content-type": "text/html; charset=utf-8"},
                )
            if request.url.path == "/attachment/9001/download":
                return httpx.Response(
                    200, content=b"%PDF-1.4 real bytes",
                    headers={"content-type": "application/pdf"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = SchoologyScraperClient(BASE_URL, VALID_USER, VALID_PASS, transport=transport)
        try:
            result = await client.download_file(f"{BASE_URL}/course/1/materials/document/9001")
            assert result is not None
            content, content_type = result
            assert content == b"%PDF-1.4 real bytes"
            assert content_type == "application/pdf"
        finally:
            await client.aclose()

    asyncio.run(run())
