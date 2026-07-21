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
from typing import Any

import httpx
from bs4 import BeautifulSoup

_LOGIN_PATH = "/login"
_APP_DOMAIN = "https://app.schoology.com"


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
