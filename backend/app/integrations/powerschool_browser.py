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

from playwright.async_api import async_playwright

from app.integrations.powerschool_client import PowerSchoolAuthError, _LOGIN_PATHS

_MOBILE_SAFARI_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


class BrowserLoginError(PowerSchoolAuthError):
    pass


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
