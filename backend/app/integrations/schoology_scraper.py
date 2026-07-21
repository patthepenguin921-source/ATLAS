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
districts which sometimes need Playwright (`powerschool_browser.py`). That
assumption is unverified against real credentials (this was built by
inspecting the public, unauthenticated login page only) — `login()` is
written defensively and `debug_materials_page()` exists specifically to
confirm real authenticated behavior without guessing further.
"""
from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

_LOGIN_PATH = "/login"


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

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        """Fetch the login form, submit it, and confirm we're in. Raises
        `SchoologyScraperAuthError` on bad credentials or an unrecognized
        login page (e.g. this district enforces SSO instead)."""
        r = await self._client.get(_LOGIN_PATH)
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", id="s-user-login-form") or next(
            (f for f in soup.find_all("form") if f.find("input", {"name": "pass"})), None
        )
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

        r = await self._client.post(_LOGIN_PATH, data=fields)
        soup = BeautifulSoup(r.text, "html.parser")
        # A failed login re-renders the same login page (still has the form);
        # a successful one lands somewhere else entirely.
        still_on_login = soup.find("form", id="s-user-login-form") is not None
        if still_on_login:
            error_text = ""
            error_div = soup.find(class_=re.compile(r"\berror\b"))
            if error_div is not None:
                error_text = error_div.get_text(" ", strip=True)
            raise SchoologyScraperAuthError(
                "Schoology login failed — check your username and password."
                + (f" ({error_text})" if error_text else "")
            )
        self._logged_in = True

    async def debug_materials_page(self, section_id: str) -> dict[str, Any]:
        """Diagnostic: fetch one section's materials page after login and
        report its raw structure — title, every link's href/text, and a
        truncated HTML snippet — so the real authenticated page shape can be
        confirmed before writing a parser against it, the same way
        `SchoologyProvider.debug_fetch` did for the API side."""
        if not self._logged_in:
            await self.login()
        r = await self._client.get(f"/course/{section_id}/materials")
        soup = BeautifulSoup(r.text, "html.parser")
        links = [
            {"text": a.get_text(" ", strip=True), "href": a.get("href")}
            for a in soup.find_all("a", href=True)
        ]
        return {
            "final_url": str(r.url),
            "status_code": r.status_code,
            "title": soup.title.string if soup.title else None,
            "link_count": len(links),
            "links": links[:60],
            "body_html_snippet": str(soup.find("body"))[:6000] if soup.find("body") else None,
        }
