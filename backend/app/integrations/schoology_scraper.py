"""Schoology materials scraper — logs in the same way a browser does and reads
the authenticated web pages directly, bypassing the personal API key entirely.

Why this exists: a district can restrict a student's personal API key to the
Sections realm only (assignments/events, which `schoology_client.py` already
handles fine) while denying it Courses-realm access outright — including a
bare `GET /courses/{id}`, not just the folder endpoint. There is no API
endpoint left to try once that's confirmed (see `SchoologyProvider.debug_fetch`
and its docstring for the diagnostic trail). But the student can browse their
own materials at `<district>.schoology.com/course/{section_id}/materials`
just fine — that's a normal, unrestricted authenticated web session, governed
by different permissions than the API-key grant. This module replicates that
web session directly instead.

The login form (`GET /login`) turned out to be a plain server-rendered Drupal
form — `POST /login` with `mail`, `pass`, `school_nid` (the district's node
id, present as a hidden field on the login page), `op=Log in`, and Drupal's
own CSRF-ish `form_build_id`/`form_id` pair (both must be echoed back
verbatim from the page just fetched — `form_build_id` is unique per page
load). No JS execution or CAPTCHA visible on that page, so a plain `httpx`
POST — not a real browser — should be enough, unlike PowerSchool's CAS-flow
districts which sometimes need Playwright (`powerschool_browser.py`).

Confirmed against a real account: a parent account's actual course-content
links live on a *different* domain entirely — `app.schoology.com` — and its
session cookie is separate from the district subdomain's (logging in at
`<district>.schoology.com/login` does not carry over; `app.schoology.com`
served its own login page when hit directly, `?destination=` param and all).
`app.schoology.com/login` turned out to be the exact same Drupal form, just
without a district context — it needs a `school_nid` value that isn't
pre-filled there (unlike the district subdomain, where the subdomain itself
identifies the school). `login()` captures the `school_nid` the district's
own login page already carries, so `_login_app_domain()` can reuse it rather
than needing the visual "School or Postal Code" autocomplete a JS-driven
browser would use to resolve it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

_LOGIN_PATH = "/login"
_APP_DOMAIN = "https://app.schoology.com"
_MAX_MATERIALS_DEPTH = 8

# The links each enrolled course shows in the web UI's course list. A course
# can appear as any of these shapes, and which one matters: a **parent**
# account's course link is a per-student preview URL that carries the
# student's own user id (`/course/{section_id}/preview/{student_uid}/parent`),
# and that student id is exactly what's needed to reach the parent-view
# materials — without it a parent account walks the wrong (empty) pages. Each
# pattern's first group is the section id; the preview pattern's second group
# is the student uid.
_COURSE_LINK_PATTERNS = (
    re.compile(r"^/course/(\d+)/preview/(\d+)/parent$"),  # parent view: (section_id, student_uid)
    re.compile(r"^/course/(\d+)/materials$"),             # direct materials: (section_id,)
    re.compile(r"^/course/(\d+)$"),                       # course profile: (section_id,)
)
# Pages in the authenticated web UI that carry the enrolled-course list.
# Tried in order and unioned — different Schoology versions surface it on
# different ones, so probing several is more robust than betting on one.
# `/parent/home` is where a parent account's own login actually lands
# (confirmed against a real account); the plain `/home` a student account
# lands on doesn't apply there, so without it a parent login had nothing to
# crawl on the app domain at all.
_COURSE_LIST_PATHS = ("/home", "/courses", "/course/index", "/parent/home")

# Links that show up on every course's materials page regardless of what the
# course actually contains — site chrome (skip-link, course nav tabs,
# third-party app launchers, the materials-type filter sidebar, admin/export
# actions) rather than course content. Confirmed against a real materials
# page rather than guessed (see `debug_materials_page`'s docstring): this is
# the literal boilerplate a real page's link dump returned, apart from its
# one actual content folder ("Folder. Syllabus and Other Important
# Documents") — which is why folders are recognized by their "Folder. " text
# prefix (see `_KNOWN_TYPE_PREFIXES`) instead of being blocklisted by exact
# text like everything else here.
_BOILERPLATE_LINK_TEXTS = frozenset({
    "Skip to Content",
    "Course Profile",
    "Current Menu Item Materials Dropdown Materials",
    "Updates",
    "Grades",
    "Mastery",
    "Members",
    "Export",
    "All Materials",
    "Assignments",
    "Tests/Quizzes",
    "Files",
    "Links",
    "Discussions",
    "Pages",
    "Albums",
    "SCORM",
    "Web Content",
    "External Tools",
    "Assessments",
    "Managed Assessments",
})

# href *patterns* for chrome that isn't safe to blocklist by exact text
# (course/section names vary per course, installed app names vary per
# district, …) — matched structurally instead.
_BOILERPLATE_HREF_PATTERNS = (
    re.compile(r"^/course/\d+$"),  # course profile / course name link
    re.compile(r"^/course/\d+/(updates|student_grades|student_mastery|members)$"),
    re.compile(r"^/course-templates/\d+$"),
    re.compile(r"^/apps/\d+/run/course/\d+$"),  # third-party app launchers
    re.compile(r"^/school/\d+$"),
    re.compile(r"^/calendar/feed/export/course/\d+$"),
    re.compile(r"^/user/\d+$"),  # logged-in user's own profile link
)
_ADMIN_TEXT_RE = re.compile(r"^\d+\s+Admin$")  # e.g. "1 Admin"

# Schoology prefixes each real material's link text with its type for
# screen readers, e.g. "Folder. Syllabus and Other Important Documents" —
# confirmed for "Folder"; the rest mirror the same lowercase vocabulary the
# API uses for a material's own `type` field (see `SchoologyMaterial.type`
# in schoology_client.py), so they're a reasonable bet but unconfirmed
# against a real non-folder item yet. An unrecognized/missing prefix still
# comes through as a plain item (never silently dropped) — it just won't
# have `material_type` set.
_KNOWN_TYPE_PREFIXES = frozenset({
    "folder", "file", "link", "page", "document", "discussion",
    "assignment", "assessment", "album", "quiz", "test",
})

# A folder link on a materials page always points at one of these href
# shapes, whether or not the screen-reader "Folder. " text prefix survived
# into the link's `get_text()` output. Relying on the text prefix alone was
# the reported failure mode — "it sees the folder link but never opens it":
# a district whose template renders that prefix in a separate, non-inlined
# span (so `get_text` never concatenates it) would leave the folder looking
# like a plain leaf item, and a leaf item is never recursed into. Matching
# the href structurally recovers the folder regardless of the a11y text.
_FOLDER_HREF_PATTERNS = (
    re.compile(r"[?&]f=\d+"),                        # /course/{id}/materials?f={folder_id}
    re.compile(r"/course/\d+/materials/folder/\d+"),
    re.compile(r"/folder/\d+(?:$|[/?])"),
    # A bare "Materials" tab link (e.g. on a parent-view course landing page)
    # is the materials root — recurse into it rather than recording it as a
    # leaf item. On a real materials page the self-referential "All
    # Materials" links are already dropped as chrome, and the visited-set
    # stops a page walking into itself, so this is safe.
    re.compile(r"/course/\d+/materials$"),
)

# Hrefs that point at an actual downloadable attachment/document (rather than
# an external link, a page, or a discussion). Used to recognize a bare
# document link as a "File" when Schoology's a11y prefix is missing, so it's
# routed through the download path instead of being filed as a plain
# reference — the second half of the reported failure ("never downloaded the
# documents that appeared there").
_FILE_HREF_PATTERNS = (
    re.compile(r"/attachment/"),
    re.compile(r"/course/\d+/materials/document/\d+"),
    re.compile(r"/system/files/"),
    re.compile(r"/content/\d+/download"),
)


@dataclass
class MaterialLink:
    """A single real course-materials item or folder, already filtered clear
    of Schoology's page chrome (see `parse_materials_page`)."""
    name: str  # display text, with the "Folder. "/"File. " etc. type prefix stripped
    href: str
    kind: str  # "folder" or "item"
    material_type: str = ""  # the type prefix Schoology's own a11y text supplies, e.g. "Folder", "File" — "" if unrecognized
    folder_path: str = ""  # breadcrumb of parent folder names, filled in while walking


def _classify_link(text: str, href: str) -> MaterialLink | None:
    text = (text or "").strip()
    href = (href or "").strip()
    if not text or not href:
        return None
    if text in _BOILERPLATE_LINK_TEXTS or _ADMIN_TEXT_RE.match(text):
        return None
    if any(p.match(href) for p in _BOILERPLATE_HREF_PATTERNS):
        return None

    prefix, sep, rest = text.partition(". ")
    if sep and prefix.lower() in _KNOWN_TYPE_PREFIXES and rest.strip():
        name = rest.strip()
        material_type = prefix
        kind = "folder" if prefix.lower() == "folder" else "item"
    else:
        name = text
        material_type = ""
        kind = "item"

    # Structural (href-based) fallback for when Schoology's a11y text prefix
    # is absent or unrecognized. A folder href always wins — a folder that
    # slipped through as a plain item would never be recursed into (the
    # reported "sees the folder but never opens it"). A bare document link
    # gets tagged "File" so the caller downloads it instead of just recording
    # a reference.
    if kind != "folder" and any(p.search(href) for p in _FOLDER_HREF_PATTERNS):
        kind = "folder"
        material_type = material_type or "Folder"
    elif kind == "item" and not material_type and any(
        p.search(href) for p in _FILE_HREF_PATTERNS
    ):
        material_type = "File"

    return MaterialLink(name=name, href=href, kind=kind, material_type=material_type)


def parse_materials_page(links: list[dict[str, Any]]) -> list[MaterialLink]:
    """Filter a materials page's raw link dump (as returned by
    `_describe_page`) down to real course content — folders and items —
    with Schoology's page chrome (nav, app launchers, type filters,
    admin/export actions) removed. The chrome is the same on every course's
    materials page (confirmed against a real account), so it's recognized
    structurally rather than by course-specific content. Duplicate hrefs
    (the confirmed page repeats its one folder link in two different nav
    spots) collapse to a single entry."""
    out: list[MaterialLink] = []
    seen_hrefs: set[str] = set()
    for link in links:
        parsed = _classify_link(link.get("text", ""), link.get("href", ""))
        if parsed is None or parsed.href in seen_hrefs:
            continue
        seen_hrefs.add(parsed.href)
        out.append(parsed)
    return out


class SchoologyScraperAuthError(RuntimeError):
    """Raised when Schoology login fails (bad credentials, or the login page
    no longer matches the plain-form shape this client speaks)."""


class SchoologyScraperClient:
    def __init__(
        self, base_url: str, username: str, password: str,
        *, transport: httpx.BaseTransport | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._user = username
        self._pass = password
        self._client = httpx.AsyncClient(
            base_url=self._base,
            follow_redirects=True,
            timeout=30.0,
            transport=transport,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
            },
        )
        self._logged_in = False
        self._app_logged_in = False
        self._school_nid: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _find_login_form(soup: BeautifulSoup):
        return soup.find("form", id="s-user-login-form") or next(
            (f for f in soup.find_all("form") if f.find("input", {"name": "pass"})), None
        )

    async def _submit_login(self, get_url: str, post_url: str, *, school_nid: str | None = None) -> BeautifulSoup:
        """Shared mechanics for both the district-subdomain and
        app.schoology.com logins: fetch the form, fill in credentials (and
        `school_nid` when the caller already knows it — the app-domain form
        doesn't carry district context, so it can't pre-fill one), submit,
        and return the resulting page's soup for the caller to judge success
        from (each domain's login form looks slightly different, so what
        "still on the login page" means is domain-specific)."""
        r = await self._client.get(get_url)
        soup = BeautifulSoup(r.text, "html.parser")
        form = self._find_login_form(soup)
        if form is None:
            raise SchoologyScraperAuthError(
                "Could not find Schoology's login form — this district may enforce "
                "SSO (Google/Microsoft/Clever) instead of a username/password login, "
                "which this client can't automate."
            )
        fields = {
            inp.get("name"): inp.get("value", "")
            for inp in form.find_all("input") if inp.get("name")
        }
        fields.update({"mail": self._user, "pass": self._pass, "op": "Log in"})
        if school_nid:
            fields["school_nid"] = school_nid

        r = await self._client.post(post_url, data=fields)
        return BeautifulSoup(r.text, "html.parser")

    def _raise_if_login_failed(self, soup: BeautifulSoup) -> None:
        # A failed login re-renders the same login page (still has the form);
        # a successful one lands somewhere else entirely.
        if self._find_login_form(soup) is None:
            return
        error_text = ""
        error_div = soup.find(class_=re.compile(r"\berror\b"))
        if error_div is not None:
            error_text = error_div.get_text(" ", strip=True)
        raise SchoologyScraperAuthError(
            "Schoology login failed — check your username and password."
            + (f" ({error_text})" if error_text else "")
        )

    async def login(self) -> None:
        """Log into the district subdomain — confirms credentials work and
        captures `school_nid` (needed later for `_login_app_domain`). Raises
        `SchoologyScraperAuthError` on bad credentials or an unrecognized
        login page (e.g. this district enforces SSO instead)."""
        r = await self._client.get(_LOGIN_PATH)
        soup = BeautifulSoup(r.text, "html.parser")
        form = self._find_login_form(soup)
        if form is None:
            raise SchoologyScraperAuthError(
                "Could not find Schoology's login form — this district may enforce "
                "SSO (Google/Microsoft/Clever) instead of a username/password login, "
                "which this client can't automate."
            )
        school_nid_input = form.find("input", {"name": "school_nid"})
        self._school_nid = school_nid_input.get("value") if school_nid_input else None

        result_soup = await self._submit_login(_LOGIN_PATH, _LOGIN_PATH)
        self._raise_if_login_failed(result_soup)
        self._logged_in = True

    async def _login_app_domain(self) -> None:
        """`app.schoology.com` has its own session, separate from the
        district subdomain's — confirmed against a real account (logging in
        at the district subdomain and then requesting an app.schoology.com
        URL just bounced back to a login page there, `?destination=` and
        all). Its login form is the same shape but district-agnostic, so it
        needs `school_nid` supplied explicitly instead of pre-filled."""
        if not self._logged_in:
            await self.login()
        if self._app_logged_in:
            return
        login_url = f"{_APP_DOMAIN}{_LOGIN_PATH}"
        result_soup = await self._submit_login(login_url, login_url, school_nid=self._school_nid)
        self._raise_if_login_failed(result_soup)
        self._app_logged_in = True

    async def list_courses(self) -> list[dict[str, str | None]]:
        """Discover the logged-in user's enrolled courses straight from the
        authenticated web UI, no API key required — the thing that makes a
        login-only account usable at all (materials can only be walked once a
        section id is known, and without the API there was no way to learn
        them). Returns one dict per course:
          - ``id``: the section id.
          - ``name``: the course display name (the link text).
          - ``student_uid``: for a **parent** account, the student's user id
            parsed out of the course's `/course/{id}/preview/{uid}/parent`
            link — needed to reach the parent-view materials. ``None`` for a
            normal student account (its links carry no such id).
        Collects across the district subdomain and app.schoology.com (where a
        parent's courses live), merging by section id."""
        if not self._logged_in:
            await self.login()
        found: dict[str, dict[str, str | None]] = {}

        def _collect(html: str) -> None:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                for pat in _COURSE_LINK_PATTERNS:
                    m = pat.match(href)
                    if not m:
                        continue
                    sid = m.group(1)
                    uid = m.group(2) if pat.groups >= 2 else None
                    name = a.get_text(" ", strip=True)
                    entry = found.setdefault(
                        sid, {"id": sid, "name": None, "student_uid": None}
                    )
                    if name and not entry["name"]:
                        entry["name"] = name
                    if uid and not entry["student_uid"]:
                        entry["student_uid"] = uid
                    break  # first (most specific) matching pattern wins

        for path in _COURSE_LIST_PATHS:
            try:
                r = await self._client.get(f"{self._base}{path}")
            except Exception:  # noqa: BLE001
                continue
            _collect(r.text)

        # Parent accounts' courses can live only on app.schoology.com (a
        # separate session — see this module's docstring). Best-effort: log
        # in there and read the same course-list pages; a failure here just
        # leaves whatever the district subdomain already found.
        try:
            await self._login_app_domain()
            for path in _COURSE_LIST_PATHS:
                try:
                    r = await self._client.get(f"{_APP_DOMAIN}{path}")
                except Exception:  # noqa: BLE001
                    continue
                _collect(r.text)
        except SchoologyScraperAuthError:
            pass

        return [e for e in found.values() if e["name"]]

    async def _describe_page(self, url: str) -> dict[str, Any]:
        r = await self._client.get(url)
        soup = BeautifulSoup(r.text, "html.parser")
        links = [
            {"text": a.get_text(" ", strip=True), "href": a.get("href")}
            for a in soup.find_all("a", href=True)
        ]
        return {
            "requested_url": url,
            "final_url": str(r.url),
            "status_code": r.status_code,
            "title": soup.title.string if soup.title else None,
            "link_count": len(links),
            "links": links[:60],
            "body_html_snippet": str(soup.find("body"))[:6000] if soup.find("body") else None,
        }

    async def debug_materials_page(self, section_id: str, student_uid: str | None = None) -> dict[str, Any]:
        """Diagnostic: fetch a section's materials page after login and
        report its raw structure — title, every link's href/text, and a
        truncated HTML snippet — so the real authenticated page shape can be
        confirmed before writing a parser against it, the same way
        `SchoologyProvider.debug_fetch` did for the API side.

        Tries every candidate URL independently (one failing/redirecting
        doesn't hide the others' results) since it's genuinely unclear which
        one this account can actually reach: the district subdomain
        (`{base}/course/{id}/materials`) is where login happens, but a parent
        account's own browser link for a course's home page is on a
        completely different domain — `app.schoology.com/course/{id}/preview/
        {student_uid}/parent` — and that domain's session cookie (if any) may
        not be the same one login established, since cookies don't cross
        domains by default. `student_uid` is the Schoology numeric id of the
        student being previewed (from that URL's own path, or
        `SchoologyClient.current_user_id()` against the API)."""
        if not self._logged_in:
            await self.login()

        candidates = {"district_materials": f"{self._base}/course/{section_id}/materials"}
        if student_uid:
            try:
                await self._login_app_domain()
                candidates["app_preview_parent"] = (
                    f"{_APP_DOMAIN}/course/{section_id}/preview/{student_uid}/parent"
                )
            except SchoologyScraperAuthError as e:
                return {
                    "district_materials": await self._describe_page(candidates["district_materials"]),
                    "app_preview_parent": {"error": f"app.schoology.com login failed: {e}"},
                }

        results: dict[str, Any] = {}
        for name, url in candidates.items():
            try:
                results[name] = await self._describe_page(url)
            except Exception as e:  # noqa: BLE001
                results[name] = {"requested_url": url, "error": str(e)}
        return results

    async def _walk_materials_tree(
        self, start_url: str, *, known_names: set[str] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> list[MaterialLink]:
        """Depth-first walk of one materials page and every real folder
        inside it, starting from `start_url` exactly and nothing else — the
        recursive core shared by `walk_materials` (which tries several
        candidate starting URLs when the right one isn't known) and
        `walk_known_url` (which is handed the one already-confirmed URL and
        never guesses at any other). Folders are never returned themselves,
        only recursed into (`parse_materials_page` strips Schoology's page
        chrome from each page's raw link dump first).

        `known_names` is the set of item names (case-insensitive) already
        pulled for this course on a previous scan; anything matching is
        skipped, so a re-scan only returns what's actually new.

        Items are matched by name rather than a stable Schoology id — the
        scraped HTML doesn't expose one for a bare item the way the API's
        folder listing does (see `SchoologyClient.walk_materials`'s bare-item
        fallback) — so a folder that's renamed, or two items that legitimately
        share a name across different folders (distinguished here by
        `folder_path`, but not by the name-only dedupe), are known
        limitations of this path.
        """
        known = {n.strip().lower() for n in (known_names or set()) if n and n.strip()}

        found: list[MaterialLink] = []
        seen_names: set[str] = set()
        visited: set[str] = set()

        async def _walk(url: str, folder_path: str, depth: int) -> None:
            if depth > _MAX_MATERIALS_DEPTH or url in visited:
                return
            visited.add(url)
            r = await self._client.get(url)
            soup = BeautifulSoup(r.text, "html.parser")
            raw_links = [
                {"text": a.get_text(" ", strip=True), "href": a.get("href")}
                for a in soup.find_all("a", href=True)
            ]
            parsed = parse_materials_page(raw_links)
            if trace is not None:
                # Surface why a walk came back empty: a session bounced to the
                # login page, a real materials page that genuinely parsed to
                # zero content links, and a JavaScript-rendered shell (whose
                # real content never arrives over plain HTTP) all look
                # identical in the final result (nothing) but mean completely
                # different things. `sample_links` and the JS-shell signals
                # expose which one it actually is — the raw material needed to
                # write a correct parser (or to know the content is only
                # reachable via a data endpoint / rendered client-side).
                body = soup.find("body")
                body_text_len = len(body.get_text(strip=True)) if body else 0
                script_count = len(soup.find_all("script"))
                trace.append({
                    "requested_url": url,
                    "final_url": str(r.url),
                    "status_code": r.status_code,
                    "depth": depth,
                    "title": (soup.title.string if soup.title else None),
                    "raw_link_count": len(raw_links),
                    "parsed_count": len(parsed),
                    "looks_like_login": self._find_login_form(soup) is not None,
                    # A page with scripts but almost no visible text/anchors is
                    # a client-rendered shell — the content isn't in the HTML.
                    "likely_js_shell": (
                        body_text_len < 400 and script_count >= 3 and len(raw_links) < 8
                    ),
                    "script_count": script_count,
                    "body_text_len": body_text_len,
                    "folders": [p.name for p in parsed if p.kind == "folder"],
                    "items": [p.name for p in parsed if p.kind == "item"],
                    # Every anchor on the page, verbatim — so the real page
                    # structure is visible instead of just a count.
                    "sample_links": raw_links[:50],
                })
            for link in parsed:
                resolved_href = urljoin(url, link.href)
                if link.kind == "folder":
                    child_path = f"{folder_path}/{link.name}" if folder_path else link.name
                    await _walk(resolved_href, child_path, depth + 1)
                    continue
                name_key = link.name.strip().lower()
                if name_key in known or name_key in seen_names:
                    continue
                seen_names.add(name_key)
                found.append(MaterialLink(
                    name=link.name, href=resolved_href, kind=link.kind,
                    material_type=link.material_type, folder_path=folder_path,
                ))

        await _walk(start_url, "", 0)
        return found

    async def walk_known_url(
        self, start_url: str, *, known_names: set[str] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> list[MaterialLink]:
        """Depth-first walk of exactly `start_url` and nothing else — no
        guessing at alternate candidate shapes (district subdomain vs
        app.schoology.com, `/materials` vs `/preview/{uid}/parent`) the way
        `walk_materials` does. For use with a course whose real materials
        page is already confirmed (see `course_mapping.KNOWN_SECTIONS`) —
        the caller already knows which single URL is correct and wants only
        that one requested, not every candidate Schoology could plausibly
        use. Logs into app.schoology.com first when `start_url` is on that
        domain (the district-subdomain login alone doesn't carry a session
        there — see this module's docstring). A failed app-domain login is
        reported through `trace` (when supplied) rather than raised — the
        caller's district-subdomain login already succeeded (that's how it
        got this far), so this shouldn't blow up the whole request the way
        letting the exception propagate would; it's no different from
        `walk_materials`/`debug_materials_page` treating that failure as
        "this candidate is unreachable" rather than fatal."""
        if not self._logged_in:
            await self.login()
        if start_url.startswith(_APP_DOMAIN):
            try:
                await self._login_app_domain()
            except SchoologyScraperAuthError as e:
                if trace is not None:
                    trace.append({"requested_url": start_url, "error": str(e)})
                return []
        return await self._walk_materials_tree(start_url, known_names=known_names, trace=trace)

    async def walk_materials(
        self, section_id: str, *, known_names: set[str] | None = None,
        student_uid: str | None = None, trace: list[dict[str, Any]] | None = None,
    ) -> list[MaterialLink]:
        """Depth-first walk of a section's materials page and every folder
        inside it, returning only real, new leaf items, by trying several
        candidate starting URLs in turn — unlike `walk_known_url`, the
        caller doesn't already know which one is right for this course.

        `known_names` is the set of item names (case-insensitive) already
        pulled for this course on a previous scan; anything matching is
        skipped, so a re-scan only returns what's actually new. Pass
        `None`/empty on a first scan (or to force a full re-pull).

        `student_uid` additionally walks the app.schoology.com parent-preview
        URL (`/course/{id}/preview/{student_uid}/parent`) — a parent
        account's real materials often live there instead of the district
        subdomain's own `/materials` page (confirmed against a real account;
        see `debug_materials_page`'s docstring — the district URL alone can
        come back with nothing to walk for a parent account, which looked
        exactly like "no materials" rather than "wrong URL"). Pass it
        whenever it's known (e.g. from `SchoologyClient.current_user_id()`).
        A failed app-domain login there is swallowed — whatever the district
        walk already found still stands.
        """
        if not self._logged_in:
            await self.login()

        found = await self._walk_materials_tree(
            f"{self._base}/course/{section_id}/materials",
            known_names=known_names, trace=trace,
        )
        # Names already found by the district walk are threaded into the
        # app-domain walk's own known-names set so the same item isn't
        # duplicated if it shows up on both — matching the single shared
        # dedupe set this used before being split into two
        # `_walk_materials_tree` calls.
        seen = set(known_names or set()) | {i.name.strip().lower() for i in found}

        # A parent account reaches a course through app.schoology.com, and its
        # real materials hang off the per-student parent-view URL, not the
        # district `/materials` page (which comes back empty — the reported
        # symptom). Load the parent-view landing first (that's what puts the
        # session into the child's context), then walk the app-domain
        # materials page — the landing's own "Materials" tab link is treated
        # as a folder (see _FOLDER_HREF_PATTERNS) so it's followed if the
        # direct URL guess is wrong. A failed app-domain login is swallowed —
        # whatever the district walk already found still stands.
        try:
            await self._login_app_domain()
            if student_uid:
                # The parent-view landing IS the course page for a parent —
                # walk it first (that GET also puts the session into the
                # child's context), following its content and its "Materials"
                # tab link. Then walk the materials page directly, now that
                # the context is set — the plain /materials URL comes back
                # empty without it (the reported "0 of N links" symptom).
                preview_found = await self._walk_materials_tree(
                    f"{_APP_DOMAIN}/course/{section_id}/preview/{student_uid}/parent",
                    known_names=seen, trace=trace,
                )
                found += preview_found
                seen |= {i.name.strip().lower() for i in preview_found}
            found += await self._walk_materials_tree(
                f"{_APP_DOMAIN}/course/{section_id}/materials",
                known_names=seen, trace=trace,
            )
        except SchoologyScraperAuthError:
            pass

        return found

    async def download_file(self, url: str) -> tuple[bytes, str] | None:
        """Fetch a materials-page item's href (already an absolute URL —
        `walk_materials` resolves relative ones) through the authenticated
        session and return `(content, content_type)` for the underlying file.

        Many Schoology material links don't point at the file directly —
        they open an HTML *viewer/detail page* that embeds (or links to) the
        real attachment. That's the reported "it never downloaded the
        documents": the first response is `text/html`, so a naive check gives
        up and the file is only ever recorded as a reference. Instead, when
        the response is HTML, this looks inside it for the actual download URL
        (a download link/button, or an embedded PDF/doc viewer's source) and
        follows that — up to a couple of hops — until it reaches a real
        (non-HTML) file. Only when no downloadable target can be found does it
        return `None`, so the caller can fall back to a plain reference rather
        than ingesting a raw HTML page as if it were a document."""
        if not self._logged_in:
            await self.login()
        return await self._download_following_detail(url, depth=0, seen=set())

    async def _download_following_detail(
        self, url: str, *, depth: int, seen: set[str],
    ) -> tuple[bytes, str] | None:
        if url in seen or depth > 2:
            return None
        seen.add(url)
        r = await self._client.get(url)
        content_type = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and content_type != "text/html":
            return r.content, content_type
        if not content_type:
            return None
        # HTML detail/viewer page — dig out the real download target(s) and
        # follow each in turn until one resolves to an actual file.
        for candidate in self._extract_download_urls(str(r.url), r.text):
            result = await self._download_following_detail(
                candidate, depth=depth + 1, seen=seen,
            )
            if result is not None:
                return result
        return None

    @staticmethod
    def _extract_download_urls(page_url: str, html: str) -> list[str]:
        """Ordered, de-duplicated list of candidate direct-download URLs
        found on a Schoology file viewer/detail page: explicit download
        links/buttons first, then embedded viewer sources (the iframe/embed a
        PDF or Office doc is previewed in). Relative URLs are resolved against
        the page they were found on; the page itself is never returned."""
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[str] = []

        def _add(raw: str | None) -> None:
            if not raw:
                return
            resolved = urljoin(page_url, raw)
            if resolved != page_url and resolved not in candidates:
                candidates.append(resolved)

        # Explicit "download" affordances.
        for a in soup.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(" ", strip=True).lower()
            if (
                re.search(r"/attachment/\d+/", href)
                or "/download" in href.lower()
                or "download" in (a.get("class") and " ".join(a["class"]).lower() or "")
                or label in {"download", "download file"}
            ):
                _add(href)
        # Embedded document viewers (PDF/Office preview iframe or <embed>).
        for tag in soup.find_all(["iframe", "embed", "object"]):
            src = tag.get("src") or tag.get("data")
            if src and any(
                p.search(src) for p in _FILE_HREF_PATTERNS
            ):
                _add(src)
        # Any other attachment-shaped link on the page as a last resort.
        for a in soup.find_all("a", href=True):
            if any(p.search(a["href"]) for p in _FILE_HREF_PATTERNS):
                _add(a["href"])
        return candidates
