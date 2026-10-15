"""Drive the system Brave over CDP with a dedicated, persistent profile.

Brave is started as a normal browser (no automation flags) so Google sign-in
works; Playwright only attaches to it over the remote-debugging port.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request

from playwright.sync_api import Browser, Page, Playwright

from .paths import PROFILE_DIR

PORT = 9333
BROWSER_CANDIDATES = ("brave-origin", "brave", "brave-browser", "chromium", "google-chrome-stable")


def _cdp_alive(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as r:
            return "webSocketDebuggerUrl" in json.load(r)
    except OSError:
        return False


def find_browser() -> str:
    for name in BROWSER_CANDIDATES:
        if path := shutil.which(name):
            return path
    raise SystemExit("No Chromium-based browser found (tried: " + ", ".join(BROWSER_CANDIDATES) + ")")


def launch(start_url: str = "about:blank", port: int = PORT) -> subprocess.Popen | None:
    """Start the browser unless one is already listening on the port."""
    if _cdp_alive(port):
        return None
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            find_browser(),
            f"--user-data-dir={PROFILE_DIR}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,1000",
            start_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if _cdp_alive(port):
            return proc
        if proc.poll() is not None:
            raise SystemExit(f"Browser exited early (code {proc.returncode})")
        time.sleep(0.2)
    raise SystemExit("Browser did not open its debugging port within 20s")


def connect(pw: Playwright, port: int = PORT) -> tuple[Browser, Page]:
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.bring_to_front()
    return browser, page


def logged_in(page: Page) -> bool:
    return bool(page.evaluate("() => !!(window.ytcfg && ytcfg.get && ytcfg.get('LOGGED_IN'))"))
