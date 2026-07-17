"""Unofficial PowerSchool Guardian/Student portal client.

PowerSchool has no public API for individual students or parents — only
district-issued OAuth credentials via a server-side plugin, which almost no
individual user has. This client instead logs in the same way a browser
does: it fetches the portal's login page, replicates PowerSchool's
client-side password-hashing handshake, and scrapes the authenticated grades
pages. This mirrors the approach used by several independent open-source
PowerSchool clients (e.g. github.com/grantpauker/powerschool-api).

The login handshake below is corroborated by multiple independent
implementations, so it's fairly trustworthy. The HTML scraping (especially
`fetch_assignments`) is inherently version/district-dependent — it's written
defensively (header-driven column detection, graceful empty-result fallback)
but may need tuning against a real district's PowerSchool instance.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

# Districts serve their login form from different URLs. '/public/home.html'
# is the common combined Parent+Student tabbed login page; some install
# versions serve the form directly at '/guardian/home.html' instead. Try both.
_LOGIN_PATHS = ("/public/home.html", "/guardian/home.html")


class PowerSchoolAuthError(RuntimeError):
    pass


class UnsupportedLoginFlow(PowerSchoolAuthError):
    """Raised when a login form is found but isn't the legacy contextData
    flow this client speaks (e.g. PowerSchool's newer CAS-based flow) — a
    distinct case from "wrong credentials" or "no form found at all", so
    callers can react differently (e.g. fall back to real-browser
    automation instead of just failing)."""


@dataclass
class PSClass:
    ccid: str
    period: str
    name: str
    teacher: str
    grade_letter: str | None
    grade_percent: float | None
    detail_href: str | None


@dataclass
class PSAssignment:
    name: str
    category: str
    due_date: str | None
    score: float | None
    points_possible: float | None
    percentage: float | None
    is_missing: bool = False
    is_late: bool = False
    is_exempt: bool = False


def _hash_password(password: str, context_data: str) -> str:
    """Replicates PowerSchool's login-page JS: `hex_hmac_md5(pskey, b64_md5(pw))`."""
    md5_digest = hashlib.md5(password.encode()).digest()
    b64_no_pad = base64.b64encode(md5_digest).decode().rstrip("=")
    return hmac.new(context_data.encode(), b64_no_pad.encode(), hashlib.md5).hexdigest()


# PowerSchool has (at least) three login form shapes in the wild:
#  - "legacy": has a `contextData` field — the MD5/HMAC dbpw handshake
#    `login()` implements below (older installs).
#  - "pcas": the current PowerSchool sign-in page. It carries the
#    `credentialType`/`pcasServerUrl`/`serviceTicket` fields *and* a real
#    username/password form (an `account` text input + a `pw` password
#    input). Its client-side `doPCASLogin()` just copies the *plaintext*
#    password into `dbpw` and posts the form — the server does the
#    credential exchange, so there's no client-side hashing to replicate.
#    This IS automatable with a plain POST (see `login()`).
#  - "cas": the same ticket markers but NO on-page password field — i.e. the
#    page hands off to an external IdP/SSO and there are no credentials to
#    submit here. NOT automatable by this client (needs the user's own
#    browser session cookie instead).
_LEGACY_FORM_MARKER = "contextData"
_CAS_FORM_MARKERS = {"credentialType", "pcasServerUrl", "serviceTicket"}
_LOGIN_FORM_MARKERS = {_LEGACY_FORM_MARKER, *_CAS_FORM_MARKERS}


def _has_password_field(form: Tag) -> bool:
    return form.find("input", attrs={"type": "password"}) is not None


def _classify_login_form(form: Tag) -> str | None:
    input_names = {inp.get("name") for inp in form.find_all("input") if inp.get("name")}
    if _LEGACY_FORM_MARKER in input_names:
        return "legacy"
    if input_names & _CAS_FORM_MARKERS:
        # A real username/password form (has a password field) is the current
        # "pcas" flow we can post directly; markers with no password field is a
        # ticket/SSO hand-off we can't automate.
        return "pcas" if _has_password_field(form) else "cas"
    return None


def _is_login_form(form: Tag) -> bool:
    if form.get("id") == "LoginForm":
        return True
    input_names = {inp.get("name") for inp in form.find_all("input") if inp.get("name")}
    return bool(input_names & _LOGIN_FORM_MARKERS)


_GRADE_RE = re.compile(r"\b([A-F][+-]?)(?![A-Za-z])")
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SCORE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:/|out of)\s*(\d+(?:\.\d+)?)", re.I)
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")

_CATEGORY_KEYWORDS = (
    "homework", "classwork", "quiz", "test", "exam", "project", "essay",
    "lab", "discussion", "presentation", "reading", "participation",
)
_HEADER_KEYWORDS = {
    "due_date": ("due", "date"),
    "category": ("category", "type"),
    "name": ("assignment", "name"),
    "score": ("score", "points"),
    "percentage": ("%", "percent"),
}

# Between school years/terms, PowerSchool lists each requested course with
# one of these placeholders instead of an assigned section/teacher — not a
# real class yet, so `fetch_classes` skips rows named this.
_PLACEHOLDER_COURSE_NAMES = {"not available", "unavailable", "n/a", "tba"}


def _parse_grade(text: str) -> tuple[str | None, float | None]:
    letter = m.group(1) if (m := _GRADE_RE.search(text)) else None
    percent = float(m.group(1)) if (m := _PERCENT_RE.search(text)) else None
    return letter, percent


def _grade_from_cell(text: str) -> tuple[str | None, float | None]:
    """Parse a grade only from a *short, grade-shaped* cell. Real grade cells
    are compact tokens ("A-", "92%", "A 92"); course titles ("AP Calculus AB")
    and room numbers ("F207") would otherwise trip `_GRADE_RE`'s bare-letter
    match, so anything longer than a grade token is rejected outright."""
    t = text.strip()
    if not t or len(t) > 12:
        return None, None
    return _parse_grade(t)


def _parse_score(text: str) -> tuple[float | None, float | None]:
    if m := _SCORE_RE.search(text):
        return float(m.group(1)), float(m.group(2))
    if m := _NUM_RE.search(text):
        return float(m.group(0)), None
    return None, None


def _parse_date(text: str) -> str | None:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_teacher(course_cell: Tag) -> str:
    """Pull the teacher name out of a course cell. PowerSchool renders the
    teacher as a `teacherinfo` details link (title="Details about <name>")
    and a mailto link ("Email <name>") — either yields the name once its
    boilerplate prefix is stripped."""
    info = course_cell.find("a", href=re.compile("teacherinfo", re.I))
    if info and info.get("title"):
        name = re.sub(r"^\s*Details about\s+", "", info["title"], flags=re.I).strip()
        if name:
            return name
    mail = course_cell.find("a", href=re.compile(r"^mailto:", re.I))
    if mail:
        return re.sub(r"^\s*Email\s+", "", mail.get_text(strip=True), flags=re.I).strip()
    return ""


def map_category(raw: str | None) -> str:
    if not raw:
        return "other"
    low = raw.lower()
    for key in _CATEGORY_KEYWORDS:
        if key in low:
            return key
    return "other"


def map_status(a: PSAssignment) -> str:
    if a.is_missing:
        return "missing"
    if a.is_exempt:
        return "excused"
    if a.is_late:
        return "late"
    if a.score is not None or a.percentage is not None:
        return "graded"
    return "not_started"


class PowerSchoolClient:
    def __init__(
        self, base_url: str, username: str = "", password: str = "",
        *, session_cookie: str | None = None, transport: httpx.BaseTransport | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._user = username
        self._pw = password
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AtlasAcademicAssistant/1.0)"}
        if session_cookie:
            # Districts that gate PowerSchool behind SSO (Google/Microsoft/Clever)
            # have no username/password form to automate at all — the caller
            # logs in with their own browser and pastes the resulting session
            # cookie instead. Set as a raw header rather than httpx's cookie
            # jar since we don't know the district's exact cookie name(s).
            headers["Cookie"] = session_cookie
        self._client = httpx.AsyncClient(
            base_url=self._base, follow_redirects=True, timeout=30.0,
            headers=headers, transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch_login_page(self) -> tuple[httpx.Response, BeautifulSoup, Tag | None]:
        """Try each known login path and return the first that has a login
        form — recognizing both the legacy contextData-based form and the
        newer CAS-style one, even though only the former can be automated.
        If none do, returns the last response/soup fetched (for diagnostics)
        with form=None."""
        response = soup = None
        for path in _LOGIN_PATHS:
            response = await self._client.get(path)
            soup = BeautifulSoup(response.text, "html.parser")
            form = next((f for f in soup.find_all("form") if _is_login_form(f)), None)
            if form is not None:
                return response, soup, form
        return response, soup, None

    async def probe_login_page(self) -> dict:
        """Fetch the login page and report what we found, without sending
        credentials — used to diagnose 'could not find the login form'
        without needing server log access."""
        from app.config import settings  # local import: avoid a hard app dependency in this module

        r, soup, form = await self._fetch_login_page()
        forms = [
            {
                "id": f.get("id"),
                "action": f.get("action"),
                "input_names": [i.get("name") for i in f.find_all("input") if i.get("name")],
            }
            for f in soup.find_all("form")
        ]
        return {
            "requested_url": str(r.url),
            "final_url": str(r.url),
            "status_code": r.status_code,
            "page_title": soup.title.get_text(strip=True) if soup.title else None,
            "has_login_form": form is not None,
            "login_type": _classify_login_form(form) if form is not None else None,
            # CAS logins fall back to real-browser (Playwright) automation,
            # which needs a Chromium binary and unbounded execution time —
            # neither available on Atlas's serverless hosting. The frontend
            # uses this to point straight at Session cookie mode instead of
            # suggesting a fallback that will just hang and fail.
            "browser_fallback_available": not settings.is_serverless,
            "forms": forms,
            "html_snippet": r.text[:4000],
        }

    async def verify_session(self) -> None:
        """For cookie-based auth: confirm the pasted session cookie is still
        valid, instead of doing a username/password login."""
        r = await self._client.get("/guardian/home.html")
        soup = BeautifulSoup(r.text, "html.parser")
        form = next((f for f in soup.find_all("form") if _is_login_form(f)), None)
        if form is not None:
            raise PowerSchoolAuthError(
                "Your PowerSchool session looks expired — log into PowerSchool in your "
                "browser again and paste a fresh session cookie."
            )

    async def login(self) -> None:
        r, soup, form = await self._fetch_login_page()
        if form is None:
            raise PowerSchoolAuthError(
                "Could not find the PowerSchool login form — check the portal URL "
                "(it should look like https://<district>.powerschool.com)."
            )
        login_type = _classify_login_form(form)
        if login_type == "cas":
            raise UnsupportedLoginFlow(
                "This district's PowerSchool login uses a newer ticket-based (CAS) flow "
                "this lightweight client doesn't speak."
            )
        fields = {
            inp.get("name"): inp.get("value", "")
            for inp in form.find_all("input") if inp.get("name")
        }

        if login_type == "pcas":
            # Current PowerSchool sign-in page. Its `doPCASLogin()` handler
            # copies the plaintext password into `dbpw` (the server, not the
            # browser, does the credential exchange) — so there's no
            # contextData hash to compute, we just submit the plaintext.
            dbpw = self._pw
        else:
            context_data = fields.get("contextData")
            if not context_data:
                raise PowerSchoolAuthError(
                    "PowerSchool login page did not return a contextData token."
                )
            dbpw = _hash_password(self._pw, context_data)

        fields.update({"account": self._user, "pw": self._pw, "dbpw": dbpw})
        # Only set ldappassword when the form actually has that field — the
        # pcas page omits it (it carries translator_ldappassword instead), and
        # posting spurious fields can trip stricter form validation.
        if "ldappassword" in fields:
            fields["ldappassword"] = self._pw
        # Resolve the form's action against the page it was found on (not
        # the site root) — some districts' login pages live under /public/
        # and post to a path relative to that, not to /guardian/.
        action = urljoin(str(r.url), form.get("action") or "/guardian/home.html")
        r = await self._client.post(action, data=fields)
        soup = BeautifulSoup(r.text, "html.parser")
        # A failed login re-renders a page that still contains the login form
        # (legacy pages echo the contextData field; pcas pages re-show the
        # username/password form). Either way, a login form still being present
        # means we did not get in.
        if next((f for f in soup.find_all("form") if _is_login_form(f)), None) is not None:
            raise PowerSchoolAuthError("PowerSchool login failed — check your username and password.")

    async def debug_home_page(self) -> dict:
        """Diagnostic twin of `probe_login_page` for the *authenticated*
        grades page: reports the raw HTML of each course row instead of
        parsed fields, so a district's actual column layout can be
        inspected (e.g. attendance columns before the course name, shifting
        `fetch_classes`'s fixed cell indices) without needing the student's/
        parent's own browser dev tools."""
        r = await self._client.get("/guardian/home.html")
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select('tr[id^="ccid_"]')
        table = rows[0].find_parent("table") if rows else None
        header_row = table.find("tr") if table is not None else None
        return {
            "final_url": str(r.url),
            "status_code": r.status_code,
            "ccid_row_count": len(rows),
            "header_row_html": str(header_row)[:2000] if header_row is not None else None,
            "sample_row_html": [str(row)[:3000] for row in rows[:3]],
        }

    async def fetch_classes(self) -> list[PSClass]:
        r = await self._client.get("/guardian/home.html")
        soup = BeautifulSoup(r.text, "html.parser")
        classes: list[PSClass] = []
        for row in soup.select('tr[id^="ccid_"]'):
            ccid = row["id"].split("_", 1)[1]
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            # Where the course name lives varies by district. Grids with
            # attendance columns (e.g. Lexington's, which has a full
            # Last-Week/This-Week block before the course) mark the course
            # cell as left-aligned while every other cell is centered — so
            # prefer that cell, and only fall back to the second column for
            # the simpler layouts (and the test fixtures) that lack it.
            name_cell = next((c for c in cells if c.get("align") == "left"), None)
            if name_cell is not None:
                name = next(
                    (s.strip() for s in name_cell.find_all(string=True, recursive=False) if s.strip()),
                    "",
                )
                teacher = _extract_teacher(name_cell)
            else:
                name_cell = cells[1]
                name = name_cell.get_text(strip=True)
                title_el = row.find(attrs={"title": True})
                teacher = title_el["title"] if title_el else ""

            if not name or name.strip().lower() in _PLACEHOLDER_COURSE_NAMES:
                # Between school years/terms (e.g. over the summer, before a
                # new schedule is built) PowerSchool lists each requested
                # course with a "Not Available" placeholder instead of a real
                # section/teacher — not a class the student is actually
                # taking yet, so don't import it as one.
                continue

            # Grades sit in the term columns after the course cell; scan those
            # (guarded so course titles/room numbers can't be misread as a
            # letter grade). Early in a term they're all "[ i ]" placeholders,
            # which correctly yields no grade.
            name_idx = cells.index(name_cell)
            grade_letter = grade_percent = None
            for c in cells[name_idx + 1:]:
                letter, percent = _grade_from_cell(c.get_text(strip=True))
                if letter or percent:
                    grade_letter, grade_percent = letter, percent
                    break

            links = row.find_all("a", href=True)
            detail_href = next(
                (l["href"] for l in links if re.search(r"scores|grade|assignment", l["href"], re.I)),
                links[-1]["href"] if links else None,
            )

            classes.append(PSClass(
                ccid=ccid,
                period=cells[0].get_text(strip=True),
                name=name,
                teacher=teacher,
                grade_letter=grade_letter,
                grade_percent=grade_percent,
                detail_href=detail_href,
            ))
        return classes

    async def fetch_assignments(self, detail_href: str) -> list[PSAssignment]:
        r = await self._client.get(detail_href)
        soup = BeautifulSoup(r.text, "html.parser")

        table = soup.find("table", id=re.compile("assignment", re.I)) \
            or soup.find("table", class_=re.compile("assignment", re.I))
        if table is None:
            # Markup varies across PowerSchool versions — fall back to the
            # largest table on the page rather than giving up entirely.
            tables = soup.find_all("table")
            table = max(tables, key=lambda t: len(t.find_all("tr")), default=None)
        if table is None:
            return []

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        header_cells = [c.get_text(strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        col_index: dict[str, int] = {}
        for field, keywords in _HEADER_KEYWORDS.items():
            for i, cell in enumerate(header_cells):
                if any(k in cell for k in keywords):
                    col_index[field] = i
                    break

        assignments: list[PSAssignment] = []
        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            row_text = " ".join(texts).lower()

            def cell(field: str, default_idx: int) -> str:
                idx = col_index.get(field, default_idx)
                return texts[idx] if 0 <= idx < len(texts) else ""

            name = cell("name", min(2, len(texts) - 1))
            if not name:
                continue
            score, points = _parse_score(cell("score", min(3, len(texts) - 1)))
            _, percentage = _parse_grade(cell("percentage", len(texts) - 1))

            assignments.append(PSAssignment(
                name=name,
                category=cell("category", 1),
                due_date=_parse_date(cell("due_date", 0)),
                score=score, points_possible=points, percentage=percentage,
                is_missing="missing" in row_text,
                is_late="late" in row_text,
                is_exempt=("exempt" in row_text or "excused" in row_text),
            ))
        return assignments
