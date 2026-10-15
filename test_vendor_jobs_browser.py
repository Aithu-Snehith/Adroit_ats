"""Browser test (Playwright + Microsoft Edge): known vendors in the Jobs tab.

Jobs from the recruiter's known vendors show "🤝 Known vendor · N"; Browse Jobs for a consultant
creates the automatic known-vendor drafts (experience-fit only) with a summary toast and
"✓ Auto-drafted" on those rows; a second Browse Jobs creates nothing new; 1-Click Draft on a
known-vendor job says how many vendor contacts are in BCC. Empty job fields show no invented
values. Gmail is never contacted (imaplib stubbed); scrapers stubbed. Labelled test fixtures.
Run: python test_vendor_jobs_browser.py
"""
import email
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendor_jobs_ui.db")
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
        captured.append(email.message_from_bytes(raw))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP

EMAIL, PASSWORD = "vjobs-asha@example.invalid", "VJobs-Test-1"
asha = models.create_user("Asha Vjobs", EMAIL, PASSWORD, role="Recruiter")
asha_id = asha["id"] if isinstance(asha, dict) else asha
cid = models.create_candidate("Kumar Vjobs", "kumar-vjobs@example.invalid", title="Java Developer", experience_years=4,
                              gmail_account="kumar-vjobs@example.invalid", assigned_user_id=asha_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real")
conn = models.get_db_connection()
vendors.import_rows(conn, asha_id, [
    {"company": "Vendor One Test Inc", "name": "Kiran", "email": "kiran@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "Lata", "email": "lata@vendor-one.example"},
])
conn.close()


def job(title, desc, company, rec="", location=""):
    j = models.save_or_update_scraped_job({"title": title, "company": company, "source": "Dice", "job_type": "Contract",
                                          "country": "United States", "recruiter_email": rec, "location": location,
                                          "url": f"https://www.dice.com/job-detail/vj-{abs(hash(title))}", "description": desc})
    return j["id"] if isinstance(j, dict) else j


J_FIT = job("Java Developer Vendor Fit", "Experience: 3-6 Yrs.", "Vendor One Test Inc", rec="poster@vendor-one.example", location="Dallas, TX")
J_SENIOR = job("Java Developer Vendor Plus8", "Need 8+ years of experience.", "Vendor One Test Inc", rec="poster@vendor-one.example")
J_OTHER = job("Java Developer Other Co", "Java role", "Unknown Test Co", rec="x@unknown.example")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "vendor_jobs.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


def row(page, jid):
    return page.locator(f"#jobs-table-body tr[data-job-id='{jid}']")


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
        check("app.js?v=5.86.0" in page.content(), "cache-buster not bumped to 5.86.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)

        # Browse Jobs for the 4-year consultant
        page.click("a.nav-item[data-tab=candidates]")
        page.locator(f"button[onclick^='browseJobsForCandidate({cid},']").first.click()
        page.wait_for_selector(".auto-drafted-tag", timeout=20000)
        toasts = page.inner_text(".toast-container")
        check("1 known-vendor draft created in Kumar Vjobs's Gmail (2 vendor contacts in BCC)" in toasts, f"summary toast: {toasts}")
        check("9 auto-drafts left today" in toasts and "nothing is sent" in toasts, f"limit + not-sent note: {toasts}")
        check(row(page, J_FIT).locator(".vendor-badge").inner_text().strip() == "🤝 Known vendor · 2", "badge on the vendor job")
        check(row(page, J_OTHER).locator(".vendor-badge").count() == 0, "no badge on an unknown company")
        check(row(page, J_FIT).locator(".auto-drafted-tag").count() == 1, "fit job marked auto-drafted")
        check(row(page, J_SENIOR).count() == 0, "8+ years job hidden by the experience filter (never auto-drafted)")
        check(len(captured) == 1 and captured[0]["To"] == "poster@vendor-one.example"
              and sorted(captured[0]["Bcc"].split(", ")) == ["kiran@vendor-one.example", "lata@vendor-one.example"],
              f"draft headers: {[(m['To'], m['Bcc']) for m in captured]}")
        page.screenshot(path=shot)

        # no invented values for empty fields
        other = row(page, J_OTHER).inner_text()
        check("United States" not in other and "Prime Vendor" not in other, f"no invented location/company: {other}")

        # Browse again: nothing new
        page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
        page.click("a.nav-item[data-tab=candidates]")
        page.locator(f"button[onclick^='browseJobsForCandidate({cid},']").first.click()
        page.wait_for_function(f"document.querySelector(\"#jobs-table-body tr[data-job-id='{J_FIT}']\")", timeout=20000)
        page.wait_for_timeout(2500)
        check(len(captured) == 1, f"second Browse Jobs drafts nothing new ({len(captured)})")
        check("known-vendor draft" not in page.inner_text(".toast-container"), "no summary toast when nothing was drafted")

        # 1-Click Draft on the unknown-company job: no BCC note; on the vendor job: BCC note
        row(page, J_OTHER).locator(".btn-draft-job").click()
        page.wait_for_function("document.querySelector('.toast-container').innerText.includes('Gmail Draft Created')", timeout=15000)
        check("BCC" not in page.inner_text(".toast-container"), "no BCC note for an unknown company")
        page.evaluate("document.querySelectorAll('.toast').forEach(t => t.remove())")
        row(page, J_FIT).locator(".btn-draft-job").click()
        page.wait_for_function("document.querySelector('.toast-container').innerText.includes('BCC: 2 vendor contacts')", timeout=15000)
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
print(f"PASS: known vendors in Jobs (Edge) - badge, automatic drafts on Browse Jobs with summary + no repeats, 1-click BCC note. Screenshot: {shot}")
