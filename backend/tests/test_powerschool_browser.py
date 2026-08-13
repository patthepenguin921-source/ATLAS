"""Real-browser PowerSchool login (powerschool_browser.py), exercised against
a local HTTP server driven by an actual headless Chromium — not a mock.
This is the one piece of the PowerSchool integration that fundamentally
can't be validated with httpx.MockTransport, since the whole point is
executing real JS/browser behavior. It needs a Chromium binary available
(installed via `playwright install chromium`) and skips gracefully if none
is found, rather than failing CI in environments without one.
"""
from __future__ import annotations

import asyncio
import http.server
import os
import threading

import pytest

from app.integrations.powerschool_browser import (
    BrowserLoginError,
    RenderedAssignmentsFetcher,
    login_and_get_cookie_header,
)
from app.integrations.powerschool_client import parse_assignments_html

VALID_USER = "parentuser"
VALID_PASS = "correct horse battery staple"

LOGIN_PAGE = f"""
<html><body>
<div id="sign-in-content">
  <form action="/dologin" method="post">
    <input type="text" name="account" placeholder="Username">
    <input type="password" name="pw" placeholder="Password">
    <input type="submit" value="Sign In">
  </form>
</div>
</body></html>
""".encode()

HOME_PAGE_AUTHENTICATED = b"""
<html><body>
<table><tr id="ccid_777"><td>1</td><td>Chemistry</td><td>B+ (88%)</td></tr></table>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default request logging
        pass

    def _is_authenticated(self) -> bool:
        return "sessionid=realbrowser1" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        if self.path in ("/public/home.html", "/guardian/home.html"):
            if self._is_authenticated():
                body = HOME_PAGE_AUTHENTICATED
            else:
                body = LOGIN_PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        form = dict(
            (k, v.replace("+", " "))
            for k, v in (pair.split("=", 1) for pair in body.split("&") if "=" in pair)
        )
        from urllib.parse import unquote_plus
        account = unquote_plus(form.get("account", ""))
        pw = unquote_plus(form.get("pw", ""))
        if account == VALID_USER and pw == VALID_PASS:
            self.send_response(200)
            self.send_header("Set-Cookie", "sessionid=realbrowser1; Path=/")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HOME_PAGE_AUTHENTICATED)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(LOGIN_PAGE)


def _start_server(handler_cls=_Handler) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _chromium_available() -> bool:
    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_dir and os.path.exists(os.path.join(browsers_dir, "chromium")):
        return True
    # Fall back to whatever `playwright install` put in its default cache —
    # login_and_get_cookie_header(executable_path=None) will find that itself.
    return os.environ.get("CI") == "true" or os.path.exists(
        os.path.expanduser("~/.cache/ms-playwright")
    )


def _test_executable_path() -> str | None:
    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_dir:
        candidate = os.path.join(browsers_dir, "chromium")
        if os.path.exists(candidate):
            return candidate
    return None


@pytest.mark.skipif(not _chromium_available(), reason="no Chromium binary available for a real-browser test")
def test_browser_login_success():
    server = _start_server()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"

        async def run():
            return await login_and_get_cookie_header(
                base_url, VALID_USER, VALID_PASS, executable_path=_test_executable_path()
            )

        cookie_header = asyncio.run(run())
        assert "sessionid=realbrowser1" in cookie_header
    finally:
        server.shutdown()


@pytest.mark.skipif(not _chromium_available(), reason="no Chromium binary available for a real-browser test")
def test_browser_login_wrong_password_raises():
    server = _start_server()
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"

        async def run():
            await login_and_get_cookie_header(
                base_url, VALID_USER, "wrong-password", executable_path=_test_executable_path()
            )

        with pytest.raises(BrowserLoginError):
            asyncio.run(run())
    finally:
        server.shutdown()


# Mirrors the real Lexington1 account's shape (confirmed live): the initial
# scores.html response has a course-summary table but no Assignments grid at
# all -- that's fetched by a client-side `fetch()` call and injected into the
# DOM afterward. A plain HTTP GET (httpx) never sees it regardless of how
# long it waits, since it was never in that response; only something that
# actually executes the page's JS (a real browser) does.
_SCORES_SHELL = b"""
<html><body>
<table class="linkDescList">
  <tr><th>Course</th><th>Teacher</th><th>Final Grade</th></tr>
  <tr><td>AP Calculus AB</td><td>Daichendt, Ana Nicoleta</td><td>97</td></tr>
</table>
<div id="assignments-slot"></div>
<script>
fetch('/guardian/scores_data.html').then(r => r.text()).then(html => {
  document.getElementById('assignments-slot').innerHTML = html;
});
</script>
</body></html>
"""

_ASSIGNMENTS_FRAGMENT = b"""
<table>
  <tr><th>Due Date</th><th>Category</th><th>Assignment</th><th>Score</th><th>%</th></tr>
  <tr><td>08/13/2026</td><td>Quiz</td><td>Limits Quiz</td><td>81/100</td><td>81%</td></tr>
</table>
"""


class _AssignmentsHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence default request logging
        pass

    def _is_authenticated(self) -> bool:
        return "sessionid=asgn1" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        if self.path == "/guardian/scores.html":
            body = _SCORES_SHELL if self._is_authenticated() else LOGIN_PAGE
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/guardian/scores_data.html":
            # Only the *data* endpoint gates on the session cookie here, so
            # a test can tell "cookie never reached the browser" apart from
            # "the shell page itself requires it" -- confirms Playwright's
            # `add_cookies` context genuinely attaches the cookie to this
            # second, JS-triggered request too, not just the first navigation.
            body = _ASSIGNMENTS_FRAGMENT if self._is_authenticated() else b"<p>Please sign in.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.mark.skipif(not _chromium_available(), reason="no Chromium binary available for a real-browser test")
def test_rendered_assignments_fetcher_captures_js_injected_table():
    """The whole point of `RenderedAssignmentsFetcher`: a real headless
    browser executing the page's own JS (here, a `fetch()` call the initial
    HTML doesn't contain the result of) sees the Assignments table that a
    plain HTTP GET of the exact same URL never would, regardless of retries
    or delay -- confirmed against a real PowerSchool account whose immediate
    and 4s-delayed plain fetches were byte-for-byte identical (see
    `debug_assignments_page`), neither containing the table shown in the
    student's own browser at that exact URL."""
    server = _start_server(_AssignmentsHandler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"

        async def run():
            fetcher = RenderedAssignmentsFetcher(
                base_url, "sessionid=asgn1", executable_path=_test_executable_path(),
            )
            try:
                return await fetcher.fetch_rendered_html("/guardian/scores.html")
            finally:
                await fetcher.aclose()

        html = asyncio.run(run())
        assignments = parse_assignments_html(html)
        assert len(assignments) == 1
        assert assignments[0].name == "Limits Quiz"
        assert assignments[0].score == 81.0
        assert assignments[0].points_possible == 100.0
        assert assignments[0].percentage == 81.0
    finally:
        server.shutdown()


@pytest.mark.skipif(not _chromium_available(), reason="no Chromium binary available for a real-browser test")
def test_rendered_assignments_fetcher_without_the_right_cookie_gets_nothing():
    """Confirms the cookie is what actually unlocks the real content -- a
    wrong/missing session cookie must get the same "please sign in" shell a
    logged-out student would, not the real Assignments table, which is what
    proves the matching-cookie test above is genuinely exercising cookie
    propagation rather than the server just always returning real data."""
    server = _start_server(_AssignmentsHandler)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"

        async def run():
            fetcher = RenderedAssignmentsFetcher(
                base_url, "sessionid=wrong-session", executable_path=_test_executable_path(),
            )
            try:
                return await fetcher.fetch_rendered_html("/guardian/scores.html")
            finally:
                await fetcher.aclose()

        html = asyncio.run(run())
        assert parse_assignments_html(html) == []
    finally:
        server.shutdown()
