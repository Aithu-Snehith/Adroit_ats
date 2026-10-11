"""Browser test (Playwright + Microsoft Edge): the Job Hunt login page (minimal light card).

Brand "Job Hunt" on the login page, browser tab and sidebar; Show / Hide password; a wrong
password shows the error; "Keep me signed in" ticked -> the login cookie lasts ~30 days, unticked
-> it ends with the browser; no reset link to a feature that doesn't exist ("ask your admin");
fits a phone screen. No network beyond the local app. Labelled test fixture.
Run: python test_login_page_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_login_ui.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "login-ui@example.invalid", "LoginUi-Test-1"
models.create_user("Ravi Loginui", EMAIL, PASSWORD, role="Recruiter")
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "login.png")
BASE = f"http://127.0.0.1:{port}"


def check(cond, msg):
    if not cond:
        failures.append(msg)


def session_cookie(ctx):
    return next((c for c in ctx.cookies() if c["name"] == "session"), None)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"{BASE}/login")
        check(page.title() == "Job Hunt - Sign in", f"tab title: {page.title()!r}")
        check("Job Hunt" in page.inner_text(".brand") and "Adroit AI Studio" not in page.content(), "brand is Job Hunt")
        check("Ask your admin" in page.inner_text(".card") and page.locator("a", has_text="Forgot").count() == 0, "no link to a missing reset feature")
        page.fill("#password", "secret-123")
        page.click("#toggle-pw")
        check(page.get_attribute("#password", "type") == "text" and page.inner_text("#toggle-pw") == "Hide", "Show reveals the password")
        page.click("#toggle-pw")
        check(page.get_attribute("#password", "type") == "password", "Hide masks it again")
        page.screenshot(path=shot)

        # wrong password -> error on the card
        page.fill("#email", EMAIL)
        page.fill("#password", "wrong-password")
        page.click("button[type=submit]")
        page.wait_for_selector(".error")
        check("Invalid email or password" in page.inner_text(".error"), "error shown")

        # unticked -> cookie ends with the browser
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.uncheck("input[name=remember]")
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        c = session_cookie(ctx)
        check(c and c["expires"] == -1, f"not kept: browser-session cookie ({c and c['expires']})")
        check(page.title() == "Job Hunt - Recruiter Workspace" and "JOB HUNT" in page.inner_text(".brand-text"), f"dashboard renamed: {page.title()!r}")
        ctx.close()

        # ticked (the default) -> ~30 days
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"{BASE}/login")
        check(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "fits a phone screen")
        check(page.is_checked("input[name=remember]"), "Keep me signed in ticked by default")
        page.fill("#email", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        c = session_cookie(ctx)
        days = (c["expires"] - time.time()) / 86400 if c and c["expires"] > 0 else 0
        check(29 < days < 31, f"kept: cookie lasts ~30 days ({days:.1f})")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Job Hunt login in Edge - brand, Show/Hide, error, keep signed in (30 days) vs browser session, dashboard renamed, phone width. Screenshot: {shot}")
