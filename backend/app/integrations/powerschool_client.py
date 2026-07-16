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

import httpx
from bs4 import BeautifulSoup


class PowerSchoolAuthError(RuntimeError):
    pass


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


def _parse_grade(text: str) -> tuple[str | None, float | None]:
    letter = m.group(1) if (m := _GRADE_RE.search(text)) else None
    percent = float(m.group(1)) if (m := _PERCENT_RE.search(text)) else None
    return letter, percent


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
        self, base_url: str, username: str, password: str,
        *, transport: httpx.BaseTransport | None = None,
    ):
        self._base = base_url.rstrip("/")
        self._user = username
        self._pw = password
        self._client = httpx.AsyncClient(
            base_url=self._base, follow_redirects=True, timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AtlasAcademicAssistant/1.0)"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        r = await self._client.get("/guardian/home.html")
        soup = BeautifulSoup(r.text, "html.parser")
        form = next(
            (f for f in soup.find_all("form") if f.find("input", attrs={"name": "contextData"})),
            None,
        )
        if form is None:
            raise PowerSchoolAuthError(
                "Could not find the PowerSchool login form — check the portal URL "
                "(it should look like https://<district>.powerschool.com)."
            )
        fields = {
            inp.get("name"): inp.get("value", "")
            for inp in form.find_all("input") if inp.get("name")
        }
        context_data = fields.get("contextData")
        if not context_data:
            raise PowerSchoolAuthError("PowerSchool login page did not return a contextData token.")
        fields.update({
            "account": self._user,
            "pw": self._pw,
            "ldappassword": self._pw,
            "dbpw": _hash_password(self._pw, context_data),
        })
        action = form.get("action") or "/guardian/home.html"
        r = await self._client.post(action, data=fields)
        soup = BeautifulSoup(r.text, "html.parser")
        if soup.find("input", attrs={"name": "contextData"}):
            raise PowerSchoolAuthError("PowerSchool login failed — check your username and password.")

    async def fetch_classes(self) -> list[PSClass]:
        r = await self._client.get("/guardian/home.html")
        soup = BeautifulSoup(r.text, "html.parser")
        classes: list[PSClass] = []
        for row in soup.select('tr[id^="ccid_"]'):
            ccid = row["id"].split("_", 1)[1]
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            grade_letter = grade_percent = None
            for c in reversed(cells):
                letter, percent = _parse_grade(c.get_text(strip=True))
                if letter or percent:
                    grade_letter, grade_percent = letter, percent
                    break

            links = row.find_all("a", href=True)
            detail_href = next(
                (l["href"] for l in links if re.search(r"scores|grade|assignment", l["href"], re.I)),
                links[-1]["href"] if links else None,
            )

            title_el = row.find(attrs={"title": True})
            teacher = title_el["title"] if title_el else ""

            classes.append(PSClass(
                ccid=ccid,
                period=cells[0].get_text(strip=True),
                name=cells[1].get_text(strip=True) if len(cells) > 1 else "",
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
