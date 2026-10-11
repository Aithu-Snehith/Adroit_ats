"""Browser test (Playwright + Microsoft Edge): Jobs experience slider + Experience column.

Slider starts at "Any"; moving it to 4 years hides the 8+ years and senior-title jobs and shows
the "include jobs that don't state experience" checkbox; unticking it hides the unstated job;
Experience badges show where the value came from; Browse Jobs on a consultant with 4 years sets
the slider to 4. Live scrapers are stubbed (no network). Job rows are labelled test fixtures.
Run: python test_jobs_experience_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_exp_ui.db")
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
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

EMAIL, PASSWORD = "exp-ui@example.invalid", "ExpUi-Test-1"
admin = models.create_user("Exp Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
cand_id = models.create_candidate("Four Yeartest", "four@example.invalid", title="Data Analyst", experience_years=4, assigned_user_id=admin_id)
for title, desc in [("Data Analyst Range Test", "Posted on Naukri: x. Experience: 3-6 Yrs."),
                    ("Data Analyst Plus8 Test", "Need 8+ years of experience."),
                    ("Senior Data Analyst Title Test", "Dice US Contract Requisition: Senior Data Analyst"),
                    ("Data Analyst Unstated Test", "Dice US Contract Requisition: Data Analyst")]:
    models.save_or_update_scraped_job({"title": title, "company": "Test Co", "source": "Dice", "job_type": "Contract",
                                       "url": f"https://www.dice.com/job-detail/{abs(hash(title))}", "description": desc})

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


ROWS = "document.querySelectorAll('#jobs-table-body tr.job-row').length"
shot = os.path.join(tmp_dir, "jobs_exp.png")
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
        check("app.js?v=5.70.0" in page.content(), "cache-buster not bumped to 5.70.0")
        page.click("a.nav-item[data-tab=jobs]")
        page.fill("#filter-query", "data analyst")
        page.click("#btn-search-jobs")
        page.wait_for_function(f"{ROWS} === 4", timeout=20000)
        check(page.text_content("#filter-experience-value") == "Any" and not page.is_visible("#filter-exp-unstated-wrap"), "starts at Any")
        headers = [h.strip() for h in page.eval_on_selector_all("#jobs-data-table thead th", "els => els.map(e => e.textContent)")]
        check("Experience" in headers, f"Experience column: {headers}")
        title_color = page.eval_on_selector("#jobs-table-body tr.job-row td div", "el => getComputedStyle(el).color")
        check(title_color not in ("rgb(255, 255, 255)",), f"job title must be visible (was white on white): {title_color}")
        check("Data Analyst Range Test" in page.inner_text("#jobs-table-body"), "job titles show in the table")
        badges = page.eval_on_selector_all("#jobs-table-body tr.job-row", "rows => rows.map(r => r.innerText)")
        joined = " | ".join(badges)
        check("3-6 yrs" in joined and "8+ yrs" in joined and "Senior (from title)" in joined and "Not stated" in joined, f"badges: {joined}")

        # drag to 4 years
        page.eval_on_selector("#filter-experience", "el => { el.value = 4; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); }")
        page.wait_for_function(f"{ROWS} === 2", timeout=20000)
        text = page.inner_text("#jobs-table-body")
        check("Range Test" in text and "Unstated Test" in text and "Plus8" not in text and "Title Test" not in text, f"4 years: {text[:300]}")
        check(page.text_content("#filter-experience-value") == "4 yrs" and page.is_visible("#filter-exp-unstated-wrap"), "label + checkbox at 4 years")
        page.screenshot(path=shot)
        page.uncheck("#filter-exp-unstated")
        page.wait_for_function(f"{ROWS} === 1", timeout=20000)
        check("Range Test" in page.inner_text("#jobs-table-body"), "unstated hidden when unticked")
        page.check("#filter-exp-unstated")

        # Browse Jobs from a 4-year consultant sets the slider
        page.eval_on_selector("#filter-experience", "el => { el.value = 16; el.dispatchEvent(new Event('input')); }")
        page.click("a.nav-item[data-tab=candidates]")
        page.locator(f"button[onclick^='browseJobsForCandidate({cand_id},']").first.click()
        page.wait_for_function("document.getElementById('filter-experience-value').textContent === '4 yrs'", timeout=10000)
        page.wait_for_function(f"{ROWS} >= 1", timeout=20000)
        check("Plus8" not in page.inner_text("#jobs-table-body"), "Browse Jobs applies the consultant's years")
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
print(f"PASS: Jobs experience slider in Edge - Any by default, 4 yrs hides 8+/senior, unstated toggle, badges, Browse Jobs sets it. Screenshot: {shot}")
