"""Browser test (Playwright + Microsoft Edge): Paste Requirement & Draft with vendor BCC.

Pasting a requirement from a known vendor shows "🤝 Known vendor: ... N contacts will be BCC'd" with
tickable contacts (the To address is not listed); an email not in the list gets "➕ Save to my
vendors"; unticking a contact removes it from the draft; the draft's Bcc header is exactly the
ticked contacts. A requirement from an unknown company shows no vendor box.
Gmail is never contacted (imaplib stubbed). People / companies are labelled test fixtures.
Run: python test_vendor_bcc_browser.py
"""
import email
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendor_bcc_ui.db")
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
import vendors  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
captured = []


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(raw)

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP

EMAIL, PASSWORD = "bccui-asha@example.invalid", "BccUi-Test-1"
asha = models.create_user("Asha Bccui", EMAIL, PASSWORD, role="Recruiter")
asha_id = asha["id"] if isinstance(asha, dict) else asha
cid = models.create_candidate("Kumar Bccui", "kumar-bccui@example.invalid", title="Java Developer",
                              gmail_account="kumar-bccui@example.invalid", assigned_user_id=asha_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real")
conn = models.get_db_connection()
vendors.import_rows(conn, asha_id, [
    {"company": "Vendor One Test Inc", "name": "Kiran Testone", "email": "kiran@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "Lata Testone", "email": "lata@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "Mohan Testone", "email": "mohan@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "The Poster", "email": "poster@vendor-one.example"},
])
conn.close()

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "paste_bcc.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


REQ = """Job Title: Java Developer
Location: Dallas, TX (Hybrid)
Please send resumes to poster@vendor-one.example or newperson@vendor-one.example
"""

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.81.0" in page.content(), "cache-buster not bumped to 5.81.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)

        # unknown company first: no vendor box
        page.click("#btn-open-paste-draft-modal")
        page.wait_for_selector("#modal-paste-draft", state="visible")
        page.fill("#pd-raw-text", "Job Title: QA Analyst\nsend to someone@unknown-co.example")
        page.wait_for_timeout(1200)
        box_text = page.inner_text("#pd-vendor-box") if page.is_visible("#pd-vendor-box") else ""
        check("Known vendor" not in box_text, f"unknown company: no known-vendor line ({box_text!r})")
        check("someone@unknown-co.example isn't in your vendors" in box_text, f"unknown email offers Save: {box_text!r}")

        # reopen -> the box is cleared; paste the known vendor's requirement
        page.click("#btn-cancel-paste-draft")
        page.click("#btn-open-paste-draft-modal")
        check(not page.is_visible("#pd-vendor-box"), "box reset when the modal reopens")
        page.select_option("#pd-consultant-select", str(cid))
        page.fill("#pd-raw-text", REQ)
        page.wait_for_selector("#pd-vendor-box .pd-bcc", timeout=10000)
        text = page.inner_text("#pd-vendor-box")
        check("🤝 Known vendor: Vendor One Test Inc" in text and "3 contacts will be BCC'd" in text, f"box: {text}")
        check(page.input_value("#pd-email") == "poster@vendor-one.example", "To auto-filled from the text")
        check("poster@vendor-one.example" not in text.split("isn't")[0].split("BCC'd")[1], "the To address is not a BCC option")
        check("newperson@vendor-one.example isn't in your vendors" in text, "Save offered for the new email")

        # untick Lata
        page.locator("#pd-vendor-box label", has_text="lata@vendor-one.example").locator("input").uncheck()
        check("2 contacts will be BCC'd" in page.inner_text("#pd-vendor-box"), "count follows the ticks")

        # save the new person -> becomes a ticked contact
        page.click("#pd-vendor-box .pd-save-vendor")
        page.wait_for_function("document.querySelector('#pd-vendor-box').innerText.includes('newperson@vendor-one.example') && "
                               "!document.querySelector('#pd-vendor-box').innerText.includes(\"isn't in your vendors\")", timeout=10000)
        check(not page.locator("#pd-vendor-box label", has_text="lata@vendor-one.example").locator("input").is_checked(),
              "Lata stays unticked after the list is re-checked")
        check(page.input_value("#pd-title") == "Java Developer", f"title stops at the line end: {page.input_value('#pd-title')!r}")
        check(page.input_value("#pd-company") == "Vendor One Test Inc", "company filled from the matched vendor")
        page.screenshot(path=shot)

        page.click("#btn-submit-paste-draft")
        page.wait_for_function("document.getElementById('modal-paste-draft').style.display === 'none'", timeout=15000)
        check(len(captured) == 1, f"one draft created ({len(captured)})")
        if captured:
            msg = email.message_from_bytes(captured[-1])
            bcc = sorted(a.strip() for a in (msg["Bcc"] or "").split(","))
            check(bcc == ["kiran@vendor-one.example", "mohan@vendor-one.example", "newperson@vendor-one.example"], f"Bcc header: {bcc}")
            check(msg["To"] == "poster@vendor-one.example", f"To: {msg['To']}")
        toasts = page.inner_text(".toast-container")
        check("BCC: 3 vendor contacts" in toasts, f"toast mentions the BCC: {toasts}")

        # saved contact is in the Vendors tab with the company from the field / match
        page.click("a.nav-item[data-tab=vendors]")
        page.wait_for_function("document.querySelectorAll('#vendors-body tr[data-id]').length === 5", timeout=10000)
        row = page.locator("#vendors-body tr", has_text="newperson@vendor-one.example").inner_text()
        check("Vendor One Test Inc" in row, f"saved with the vendor's company: {row}")
        check(page.locator("#vendors-body tr", has_text="kiran@vendor-one.example").inner_text().count("20") >= 1, "last emailed shown for Kiran")
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
print(f"PASS: Paste & Draft vendor BCC in Edge - known vendor box, To excluded, untick, Save to my vendors, Bcc header = ticked. Screenshot: {shot}")
