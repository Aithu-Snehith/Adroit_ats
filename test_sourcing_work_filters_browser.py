"""Browser test (Playwright + Microsoft Edge): Sourcing counter + LinkedIn work checkboxes.

The Sourcing tab shows how many profiles were scraped (total / this week / matching), each
checkbox (Open to work, Contract, Full-time, Remote, Hybrid, On-site) shows its count, ticking
one narrows the table, rows carry the badges, and the "no work data" note explains older profiles.
No network beyond the local app. People are labelled test fixtures.
Run: python test_sourcing_work_filters_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_work_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "work-ui@example.invalid", "WorkUi-Test-1"
models.create_user("Hema Workui", EMAIL, PASSWORD, role="Recruiter")


def item(slug, first, otw=None, emp=None, wp=None, with_work=True):
    it = {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Workui",
          "headline": "Java Developer", "location": {"linkedinText": "Dallas, Texas"}, "currentPosition": [{"companyName": "Co"}],
          "education": [{"schoolName": "JNTUH", "degree": "B.Tech", "endDate": {"year": 2019}},
                        {"schoolName": "University of Texas at Dallas", "degree": "MS", "endDate": {"year": 2022}}]}
    if with_work:
        it["openToWork"] = otw
        it["experience"] = [{"companyName": "Co", "employmentType": emp, "workplaceType": wp, "endDate": {"text": "Present"}}]
    return it


li.ingest_profiles(li.ApifyProfileProvider().to_profiles([
    item("wu-1", "Arun", True, "Contract", "Remote"), item("wu-2", "Bindu", False, "Full-time", "Hybrid"),
    item("wu-3", "Charan", with_work=False)]), "apify", None)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "work_filters.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


ROWS = "document.querySelectorAll('#edu-results tr[data-pid]').length"
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
        check("app.js?v=5.77.0" in page.content(), "cache-buster not bumped to 5.77.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.select_option("#filter-student-bachelor-year", "All")
        page.wait_for_function(f"{ROWS} === 3", timeout=15000)
        cards = [page.inner_text(f"#edu-st-{k}") for k in ("total", "week", "match", "otw", "contact")]
        check(cards == ["3", "3", "3", "1", "0"], f"number cards (total, week, match, open to work, contact): {cards}")
        check("1 of these were scraped before" in page.inner_text("#edu-counter"), f"no-data note: {page.inner_text('#edu-counter')!r}")
        check(page.inner_text(".edu-wc[data-key=open_to_work]") == "(1)" and page.inner_text(".edu-wc[data-key=emp_fulltime]") == "(1)", "checkbox counts")
        row = page.inner_text("#edu-results tr[data-pid]:has-text('Arun')")
        check("Open to work" in row and "Contract" in row and "Remote" in row, f"row badges: {row!r}")

        page.check("#edu-f-otw")
        page.wait_for_function(f"{ROWS} === 1", timeout=10000)
        check("Arun" in page.inner_text("#edu-results"), "Open to work narrows to Arun")
        check(page.inner_text("#edu-st-match") == "1" and page.inner_text("#edu-st-total") == "3", "Match card shows the narrowed number, Total stays")
        page.uncheck("#edu-f-otw")
        page.check("#edu-f-contract")
        page.check("#edu-f-fulltime")
        page.wait_for_function(f"{ROWS} === 2", timeout=10000)
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
print(f"PASS: sourcing counter + LinkedIn work checkboxes in Edge - totals, counts, badges, narrowing. Screenshot: {shot}")
