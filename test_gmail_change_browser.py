"""Browser test (Playwright + Microsoft Edge): change or disconnect a consultant's connected Gmail.

Root cause this guards against: once a Gmail was connected, the Consultants table only showed
"Connected" - there was no way to remove it or connect a different Gmail.
Checks: the connected address + "Change Gmail" / "Disconnect" are shown; Disconnect (confirmed)
forgets the address, App Password and OAuth token file and the "App Password" button returns;
"Change Gmail" opens the form with an empty Gmail box and saving a verified new App Password
replaces the old Gmail; another recruiter can't disconnect it. Google is never contacted
(verify_gmail_app_password is stubbed). People are labelled test fixtures.
Run: python test_gmail_change_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_gmail_change.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
verified = []
gm.verify_gmail_app_password = lambda email, pw: (verified.append((email, pw)) or (True, "ok"))

EMAIL, PASSWORD = "gmailchg@example.invalid", "GmailChg-Test-1"
admin = models.create_user("Gmail Change Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
other = models.create_user("Other Recruiter", "gmailchg-other@example.invalid", "GmailChg-Test-2", role="Recruiter")
other_id = other["id"] if isinstance(other, dict) else other
vid = models.create_candidate("Vishak Gmailtest", "vishak-gmailtest@example.invalid", title="Data Engineer", assigned_user_id=admin_id)
models.update_candidate(vid, gmail_account="old-vishak@example.invalid", gmail_app_password="oldoldoldoldoldo")
token_file = gm.TOKENS_DIR / f"token_{vid}.json"
token_file.write_text("{}")   # a leftover OAuth token must be removed too

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "gmail_change.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


# another recruiter can't touch it
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": other_id, "name": "Other Recruiter", "role": "Recruiter"}
check(client.post(f"/api/consultants/{vid}/disconnect-gmail").status_code == 404, "other recruiter cannot disconnect")
check(models.get_candidate_by_id(vid)["gmail_app_password"] == "oldoldoldoldoldo", "still connected after the refused request")

ROW = f"#consultants-table-body tr:has(button[data-id='{vid}'])"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.85.0" in page.content(), "cache-buster not bumped to 5.85.0")
        page.click("a.nav-item[data-tab=candidates]")
        page.wait_for_selector(".btn-disconnect-gmail", timeout=15000)
        body = page.inner_text("#consultants-table-body")
        check("old-vishak@example.invalid" in body and "Change Gmail" in body, f"connected address + buttons shown: {body[:300]!r}")

        # Disconnect (confirm dialog accepted)
        page.once("dialog", lambda d: d.accept())
        page.click(".btn-disconnect-gmail")
        page.wait_for_selector(f"button.btn-open-app-pass[data-id='{vid}']", timeout=10000)
        c = models.get_candidate_by_id(vid)
        check(not c.get("gmail_account") and not c.get("gmail_app_password"), f"address + App Password forgotten: {c.get('gmail_account')!r}")
        check(not token_file.exists(), "OAuth token file removed")
        check("Gmail disconnected" in page.inner_text(".toast-container"), "toast confirms")

        # Connect the new Gmail
        page.click(f"button.btn-open-app-pass[data-id='{vid}']")
        page.fill("#app-pass-email", "new-vishak@example.invalid")
        page.fill("#app-pass-key", "abcd efgh ijkl mnop")
        page.click("#form-app-password button[type=submit]")
        page.wait_for_selector(".btn-change-gmail", timeout=10000)
        c = models.get_candidate_by_id(vid)
        check(c["gmail_account"] == "new-vishak@example.invalid" and c["gmail_app_password"] == "abcdefghijklmnop",
              f"new Gmail connected: {c['gmail_account']}")

        # Change Gmail opens the form with an EMPTY Gmail box and replaces the address
        page.click(".btn-change-gmail")
        page.wait_for_selector("#modal-app-password", state="visible")
        check(page.input_value("#app-pass-email") == "", "Change Gmail starts with an empty Gmail box")
        page.fill("#app-pass-email", "newer-vishak@example.invalid")
        page.fill("#app-pass-key", "qrst uvwx yzab cdef")
        page.click("#form-app-password button[type=submit]")
        page.wait_for_function("document.getElementById('consultants-table-body').innerText.includes('newer-vishak@example.invalid')", timeout=10000)
        check(models.get_candidate_by_id(vid)["gmail_account"] == "newer-vishak@example.invalid", "Change Gmail replaced the address")
        check(verified[-1] == ("newer-vishak@example.invalid", "qrstuvwxyzabcdef"), f"new App Password verified first: {verified[-1:]}")
        page.screenshot(path=shot)
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
print(f"PASS: Gmail change/disconnect in Edge - address + buttons, disconnect clears App Password + token, reconnect, Change Gmail replaces, other recruiter refused. Screenshot: {shot}")
