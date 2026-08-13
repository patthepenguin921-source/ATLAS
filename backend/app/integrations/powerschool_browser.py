"""Real-browser PowerSchool login via Playwright.

`powerschool_client.py`'s lightweight httpx-based `login()` only speaks
PowerSchool's legacy contextData/dbpw handshake. Some districts use a newer
CAS-based flow instead (see `UnsupportedLoginFlow`), often paired with
bot-mitigation (Akamai/Imperva-style sensor scripts) that a plain HTTP
client can't get past regardless of protocol — it needs a real browser
actually executing the page's JS.

This is a fallback of last resort, not a guarantee: bot-mitigation systems
commonly also weigh the *origin* of a request (IP/network reputation), and
Atlas's backend runs from cloud/datacenter infrastructure — exactly what
these systems are tuned to distrust, independent of whether a real browser
drove the request. If this still gets blocked, the honest ceiling for a
district like that is running the automation from the student/parent's own
residential network instead of Atlas's server, which this module doesn't
attempt.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from playwright.async_api import async_playwright

from app.integrations.powerschool_client import PowerSchoolAuthError, _LOGIN_PATHS

_MOBILE_SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


class BrowserLoginError(PowerSchoolAuthError):
    pass


def _cookie_header_to_playwright_cookies(cookie_header: str, base_url: str) -> list[dict]:
    domain = urlsplit(base_url).hostname
    cookies = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain, "path": "/"})
    return cookies


class RenderedAssignmentsFetcher:
    """A reusable headless-browser session for rendering however many
    courses' assignments pages a single `PowerSchoolProvider.sync()` run
    needs, instead of paying Playwright's launch cost (a real Chromium
    process) again for every course that needs it -- some PowerSchool
    skins (confirmed against a real Lexington1 account) fill the
    Assignments grid in via client-side JS after the initial page load, so
    a plain HTTP GET's response never contains it, immediately or after any
    delay. `_authenticated_client`'s already-verified session cookie is
    handed straight to the browser context instead of logging in again."""

    def __init__(
        self, base_url: str, cookie_header: str, *, executable_path: str | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._cookie_header = cookie_header
        self._executable_path = executable_path
        self._playwright = None
        self._browser = None
        self._context = None

    async def _ensure_context(self):
        if self._context is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            executable_path=self._executable_path,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = await self._browser.new_context(user_agent=_MOBILE_SAFARI_UA)
        await self._context.add_cookies(
            _cookie_header_to_playwright_cookies(self._cookie_header, self._base_url)
        )

    async def fetch_rendered_html(self, url: str) -> str:
        """Navigates to `url` (relative hrefs are resolved the same way
        `PowerSchoolClient.fetch_classes` resolves them -- against
        `/guardian/home.html`, the page they're normally clicked from) and
        waits for the network to go idle before returning the fully-rendered
        HTML, so any assignments grid that loads via a background XHR/fetch
        after the initial page load has had a chance to actually appear."""
        await self._ensure_context()
        full_url = url if url.startswith("http") else urljoin(f"{self._base_url}/guardian/home.html", url)
        page = await self._context.new_page()
        try:
            await page.goto(full_url, wait_until="networkidle", timeout=30000)
            return await page.content()
        finally:
            await page.close()

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


async def login_and_get_cookie_header(
    base_url: str, username: str, password: str, *, executable_path: str | None = None,
) -> str:
    """Drives a real headless Chromium through PowerSchool login and returns
    the resulting session as a `Cookie:` header string, ready to hand to
    `PowerSchoolClient(session_cookie=...)` for the rest of a sync.

    `executable_path` lets tests point at a pre-installed browser whose
    revision doesn't match what this `playwright` version would normally
    look for; production leaves it unset and uses Playwright's own
    (version-matched) browser from `playwright install chromium`.
    """
    base_url = base_url.rstrip("/")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=executable_path,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(user_agent=_MOBILE_SAFARI_UA)
        page = await context.new_page()
        try:
            logged_in = False
            for path in _LOGIN_PATHS:
                await page.goto(f"{base_url}{path}", wait_until="domcontentloaded")

                scope = page.locator("#sign-in-content")
                if await scope.count() == 0:
                    # Some installs serve the form directly, not inside a
                    # tabbed #sign-in-content panel — search the whole page.
                    scope = page

                account_field = scope.locator("input[type='text'], input[type='email']").first
                password_field = scope.locator("input[type='password']").first
                if await account_field.count() == 0 or await password_field.count() == 0:
                    continue

                await account_field.fill(username)
                await password_field.fill(password)
                await password_field.press("Enter")

                try:
                    await page.wait_for_selector('tr[id^="ccid_"]', timeout=20000)
                    logged_in = True
                except Exception:  # noqa: BLE001 — Playwright timeout, just means login didn't land
                    logged_in = False
                break  # only retry other login paths if we never found a form at all

            if not logged_in:
                raise BrowserLoginError(
                    "Automated browser login didn't reach your grades page. This can mean "
                    "the credentials are wrong, or that this district's bot-protection is "
                    "blocking Atlas's server specifically — a known risk with cloud/datacenter "
                    "IPs, even with a real browser driving the login."
                )

            cookies = await context.cookies()
        finally:
            await browser.close()

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
