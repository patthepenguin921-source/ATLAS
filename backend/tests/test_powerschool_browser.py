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

from app.integrations.powerschool_browser import BrowserLoginError, login_and_get_cookie_header

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


def _start_server() -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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
