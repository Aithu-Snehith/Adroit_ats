"""Browser test (Playwright + Microsoft Edge): the Applications tracker window.

Consultants -> "Applications" opens that consultant's tracker: the drafted job is listed as "Draft
created"; changing the status / follow-up date / notes saves; History shows who changed what;
"+ Add application" tracks one made outside the app; "Check Gmail replies" (stubbed, read-only)
marks a replied job "Recruiter responded"; the Excel link points at that consultant; Reporting ->
"All applications" lists every consultant. Gmail is never contacted. Labelled test fixtures.
Run: python test_application_tracker_browser.py
"""
import email
import email.policy
import io
import os
import socket
import sys
import tempfile
import threading
from datetime import date

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_tracker_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import application_tracker as at  # noqa: E402
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        pass

    def select(self, box, readonly=False):
        assert readonly, "the reply check must open the INBOX read-only"
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return ("OK", [b"3"]) if '"rec-a@vendor.example"' in criteria else ("OK", [b""])

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP
import reply_inbox  # noqa: E402
assert reply_inbox.imaplib.IMAP4_SSL is FakeIMAP

EMAIL, PASSWORD = "trkui@example.invalid", "TrkUi-Test-1"
admin = models.create_user("Tracker UI Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
d = docx.Document()
d.add_paragraph("VISAKH TRACKUI resume fixture")
buf = io.BytesIO()
d.save(buf)
cid = models.create_candidate("Visakh Trackui", "visakh-trkui@example.invalid", title="DevOps Engineer", gmail_account="visakh-trkui@example.invalid",
                              assigned_user_id=admin_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real", country="United States")
models.save_resume_file(cid, "Visakh_original.docx", buf.getvalue())
cid2 = models.create_candidate("Koushik Trackui", "koushik-trkui@example.invalid", title="Java Developer", assigned_user_id=admin_id)
ja = models.save_or_update_scraped_job({"title": "DevOps Lead Trackui", "company": "Vendor Test Co", "source": "Dice", "recruiter_email": "rec-a@vendor.example",
                                        "url": "https://www.dice.com/job-detail/trkui-a", "description": "DevOps"})
ja = ja["id"] if isinstance(ja, dict) else ja
jk = models.save_or_update_scraped_job({"title": "Java Dev Trackui", "company": "Other Test Co", "source": "Dice",
                                        "url": "https://www.dice.com/job-detail/trkui-k", "description": "Java"})
jk = jk["id"] if isinstance(jk, dict) else jk
gm.create_candidate_draft(cid, ja, acting_user_id=admin_id)          # tracked as "Draft created"
conn = models.get_db_connection()
at.upsert(conn, jk, cid2, admin_id, stage="Applied", apply_method="portal")
conn.close()

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "tracker.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


ROW = f"#trk-body tr[data-app-id]"
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
        check("app.js?v=5.78.0" in page.content(), "cache-buster not bumped to 5.78.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 2", timeout=15000)

        # Consultants -> Applications (Visakh)
        page.click("a.nav-item[data-tab=candidates]")
        page.click(f".btn-open-tracker[onclick='openTracker({cid})']")
        page.wait_for_selector(ROW, timeout=10000)
        check(page.input_value("#trk-consultant") == str(cid), "opened for Visakh")
        rows = page.locator(ROW)
        check(rows.count() == 1 and "DevOps Lead Trackui" in rows.first.inner_text(), "Visakh's drafted job listed")
        check(rows.first.locator("select[data-f=status]").input_value() == "Drafted", "status = Draft created")
        check(not page.is_visible("#trk-table th.trk-col-cand"), "no Consultant column for one consultant")
        check("Total: 1" in page.inner_text("#trk-summary"), f"summary: {page.inner_text('#trk-summary')}")

        # edits save
        rows.first.locator("select[data-f=status]").select_option("Applied")
        page.wait_for_timeout(400)
        rows.first.locator("input[data-f=follow_up_date]").fill(date.today().isoformat())
        rows.first.locator("input[data-f=follow_up_date]").dispatch_event("change")
        rows.first.locator("textarea[data-f=notes]").fill("Spoke to the vendor, client is Test Bank")
        rows.first.locator("textarea[data-f=notes]").dispatch_event("change")
        page.wait_for_timeout(800)
        conn = models.get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT stage, follow_up_date, notes FROM applications WHERE job_id = ? AND candidate_id = ?", (ja, cid))
        row = dict(cur.fetchone())
        conn.close()
        check(row["stage"] == "Applied" and row["follow_up_date"] == date.today().isoformat() and "Test Bank" in row["notes"], f"saved: {row}")
        rows.first.locator(".trk-hist").click()
        page.wait_for_selector(".trk-hist-row", timeout=5000)
        hist = page.inner_text(".trk-hist-row")
        check("Draft created -> Applied" in hist and "Tracker UI Admin" in hist, f"history: {hist}")

        # check Gmail replies (stubbed, read-only)
        page.click("#trk-replies-btn")
        page.wait_for_function("document.getElementById('trk-msg').innerText.includes('new repl')", timeout=10000)
        page.wait_for_function("document.querySelector('#trk-body select[data-f=status]').value === 'Responded'", timeout=10000)

        # + Add application
        page.click("#trk-add-btn")
        page.fill("#trk-a-title", "Platform Engineer Trackui")
        page.fill("#trk-a-company", "Outside Test Corp")
        page.fill("#trk-a-url", "https://careers.example.invalid/42")
        page.select_option("#trk-a-method", "both")
        page.click("#trk-a-save")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 2", timeout=10000)
        check(page.get_attribute("#trk-export", "href") == f"/api/applications/export-xlsx?candidate_id={cid}", "Excel for this consultant")
        page.eval_on_selector("#trk-table", "t => t.parentElement.scrollLeft = 0")
        overflow = page.eval_on_selector("#trk-table", "t => t.scrollWidth - t.parentElement.clientWidth")
        check(overflow <= 2, f"tracker table fits a 1440px window (overflow {overflow}px)")
        page.screenshot(path=shot)
        page.click("#btn-close-tracker")

        # Reporting -> All applications
        page.click("a.nav-item[data-tab=reporting]")
        page.click("#btn-open-tracker-all")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 3", timeout=10000)
        check(page.is_visible("#trk-table th.trk-col-cand") and "Koushik Trackui" in page.inner_text("#trk-body"), "all consultants with a Consultant column")
        page.check("#trk-due")
        page.wait_for_function(f"document.querySelectorAll('{ROW}').length === 1", timeout=10000)
        check("DevOps Lead Trackui" in page.inner_text("#trk-body"), "follow-ups due filter")
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
print(f"PASS: Applications tracker in Edge - per consultant, inline edits saved, history, Gmail replies, manual add, Excel, all-consultants view, follow-ups due. Screenshot: {shot}")
