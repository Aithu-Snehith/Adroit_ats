"""Browser test (Playwright + Microsoft Edge): the Jobs table's Target Candidate dropdowns must be
filled even when the job list arrives before the consultant list.

Root cause this guards against: the dashboard loads jobs and consultants at the same time; when the
jobs answer came first (common on Render's slow free server), every row's Target Candidate dropdown
was built from an empty consultant list and never refilled - blank dropdowns, so 1-Click Draft and
Optimize Resume had no consultant. The test server delays /api/consultants by 2 s to force it.
Live scrapers are stubbed (no network). People / jobs are labelled test fixtures.
Run: python test_jobs_candidate_dropdown_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_dropdown.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from flask import request  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

EMAIL, PASSWORD = "dropdown@example.invalid", "Dropdown-Test-1"
admin = models.create_user("Dropdown Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
c1 = models.create_candidate("First Droptest", "first-drop@example.invalid", title="Java Developer", assigned_user_id=admin_id)
c2 = models.create_candidate("Second Droptest", "second-drop@example.invalid", title="Data Analyst", assigned_user_id=admin_id)
for t in ("Software Engineer Drop A", "Software Engineer Drop B"):
    models.save_or_update_scraped_job({"title": t, "company": "Test Co", "source": "Dice", "job_type": "Contract",
                                       "url": f"https://www.dice.com/job-detail/{abs(hash(t))}", "description": "Contract role"})


@app_module.app.before_request
def _slow_consultants():
    if request.path == "/api/consultants":
        time.sleep(2.0)


with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


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
        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to 5.88.0")
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_function("document.querySelectorAll('#jobs-table-body tr.job-row').length >= 2", timeout=20000)
        # consultants arrive ~2 s later: the dropdowns must then be filled
        page.wait_for_function("[...document.querySelectorAll('.job-consultant-select')].every(s => s.options.length === 2)", timeout=15000)
        opts = page.eval_on_selector_all(".job-consultant-select", "els => els.map(s => [...s.options].map(o => o.text.trim()))")
        check(all(o == ["First Droptest (Java Developer)", "Second Droptest (Data Analyst)"] for o in opts), f"dropdown options: {opts}")
        vals = page.eval_on_selector_all(".job-consultant-select", "els => els.map(s => s.value)")
        check(all(v in (str(c1), str(c2)) for v in vals), f"a consultant is selected in every row: {vals}")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: Target Candidate dropdowns are filled even when jobs load before consultants.")
