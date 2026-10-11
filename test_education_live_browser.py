"""Browser test (Playwright + Microsoft Edge): "Search LinkedIn" on the Filter A tab searches the
chosen college and the verified people appear in the table.

Apify is stubbed in-process (start_search / fetch_run): run 1 returns two profiles (one verified),
run 2 returns none (LinkedIn has no more), so the search ends. Requests to api.apify.com are
aborted. People are labelled test fixtures.
Run: python test_education_live_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_live_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = "dummy-test-token-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "dummy-test-token-not-real"
import app as app_module  # noqa: E402
import linkedin_sourcing as ls  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def edu(school, degree, end):
    return {"schoolName": school, "degree": degree, "endDate": {"year": end}}


def person(slug, first, education):
    return {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Uilive",
            "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas", "countryCode": "US"},
            "currentPosition": [{"position": "Data Engineer", "companyName": "Test Co"}], "education": education}


started = []
PAGES = {1: [person("ui-live-1", "Kiran", [edu("JNTU Kakinada", "B.Tech", 2018), edu("Example State University Testville", "MS", 2020)]),
             person("ui-live-2", "Lata", [edu("Osmania University", "BE", 2016), edu("UT Dallas", "MS", 2018)]),
             # JNTU + US Master's but passed out in 2019 - not the chosen 2018 -> never shown
             person("ui-live-3", "Mohan", [edu("JNTUH", "B.Tech", 2019), edu("Example State University Testville", "MS", 2021)])]}


def fake_start(bachelor_year, pages=1, location="United States", start_page=1, schools=None, experience_ids=None):
    started.append({"start_page": start_page, "schools": schools})
    rid = f"RunUi{start_page:04d}AAAAA"
    return {"runs": [{"run_id": rid, "dataset_id": f"Data{rid}", "start_page": start_page}], "pages_started": 1,
            "next_start_page": start_page + 1, "max_spend_usd": 0.31, "spent_today_usd": 0}


def fake_fetch(run_id, dataset_id, offset=0):
    page_no = int(run_id[5:9])
    return {"run": {"usageTotalUsd": 0.2, "statusMessage": ""}, "status": "SUCCEEDED",
            "raw_items": PAGES.get(page_no, [])[offset:]}


ls.start_search = fake_start
ls.fetch_run = fake_fetch
ls._provider_problem = lambda run_id, status, msg="": ("", "")

EMAIL, PASSWORD = "live-ui-admin@example.invalid", "LiveUi-Test-1"
models.create_user("Live UI Admin", EMAIL, PASSWORD, role="Admin")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

failures, js_errors, blocked = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def block(route):
    blocked.append(route.request.url)
    route.abort()


shot = os.path.join(tmp_dir, "live_filter_a.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        ctx.add_init_script("window.EDU_LIVE_POLL_MS = 100;")
        page = ctx.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/api.apify.com/**", block)
        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.72.0" in page.content(), "cache-buster not bumped to 5.72.0")

        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        check(page.is_visible("#btn-apply-student-filter"), "Search LinkedIn must be on the Filter A tab")
        # without a college it explains, and starts nothing
        page.click("#btn-apply-student-filter")
        check("Pick an Indian college first" in page.inner_text("#edu-status") and not started, "needs a college first")

        page.fill("#edu-inst-input", "jntu")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")   # JNTU - any campus
        page.wait_for_function("document.getElementById('edu-status').innerText.includes('JNTU - any campus')")
        # without a passout year it explains, and starts nothing (a paid search needs the exact year)
        page.click("#btn-apply-student-filter")
        check("Choose the Bachelor's passout year first" in page.inner_text("#edu-status") and not started, "needs a passout year first")
        page.select_option("#edu-passout-year", "2018")
        page.wait_for_timeout(500)
        page.click("#btn-apply-student-filter")
        page.wait_for_function("document.getElementById('students-search-status').innerText.includes('Finished')", timeout=20000)
        status = page.inner_text("#students-search-status")
        check("scanned 3 LinkedIn profiles" in status and "1 new verified person" in status and "$0.40" in status
              and "no more profiles" in status, f"summary: {status!r}")
        check(len(started) == 2 and started[0]["start_page"] == 1 and started[1]["start_page"] == 2
              and "Jawaharlal Nehru Technological University Kakinada" in started[0]["schools"], f"runs: {started}")
        page.wait_for_function("document.getElementById('edu-results').innerText.includes('Kiran Uilive')", timeout=10000)
        table = page.inner_text("#edu-results")
        check("Kiran Uilive" in table and "Lata Uilive" not in table and "Mohan Uilive" not in table, f"table after search (2018 only): {table!r}")
        check(not page.is_disabled("#btn-apply-student-filter"), "button re-enabled after the search")
        page.screenshot(path=shot)

        # picking a DIFFERENT college clears the previous search's summary (it no longer describes the table)
        page.fill("#edu-inst-input", "osmania")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")
        page.wait_for_function("document.getElementById('students-search-status').innerText.trim() === ''", timeout=5000)

        # Passout tab still uses its own search (button routes there; not exercised - it would start the Passout flow)
        page.click(".edu-tab[data-filter=P]")
        check(page.inner_text("#students-search-status").strip() == "", "switching tab clears the previous search summary")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not blocked, f"a real Apify call was attempted: {blocked}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Filter A Search LinkedIn in Edge - chosen college searched page by page, verified person shown, cost + summary, no real Apify call. Screenshot: {shot}")
