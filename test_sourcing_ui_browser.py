"""Browser test (Playwright + Microsoft Edge) for the Sourcing tab after People Data Labs was removed.

Starts the real Flask app on a throwaway SQLite DB, logs in through the real login form, opens
Sourcing and checks: no JavaScript errors, the data-source list offers Apify only, the search-depth
control is visible, no PDL text anywhere, and the team pool (incl. people PDL found earlier) shows.

COST SAFETY: "Search LinkedIn" is never clicked, every request to api.apify.com and the paid
search endpoints is aborted, and the Apify token is a dummy value - this test cannot spend money.
Run: python test_sourcing_ui_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = "dummy-test-token-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = "dummy-test-token-not-real"
import app as app_module  # noqa: E402
import linkedin_ingest  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "ui-test-admin@example.invalid", "UiTest-Only-Pass-123"
models.create_user("UI Test Admin", EMAIL, PASSWORD, role="Admin")
conn = models.get_db_connection()
cur = conn.cursor()
for src, slug, name in (("pdl", "a", "Test Pool Person From PDL"), ("apify", "b", "Test Pool Person From Apify")):
    cur.execute("INSERT INTO sourced_candidates (source, search_year, profile_url, name, bachelor_year, bachelor_college, master_university) "
                "VALUES (?, '2019', ?, ?, '2019', 'Osmania University', 'UT Dallas')",
                (src, f"https://www.linkedin.com/in/test-only-{slug}.example.invalid", name))
conn.commit()
conn.close()
linkedin_ingest.import_sourced_pool()   # what startup does on the server

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
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


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/api.apify.com/**", block)
        page.route("**/api/students/search-start**", block)
        page.route("**/api/students/search-poll**", block)

        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")

        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to app.js?v=5.88.0")

        page.click("a.nav-item[data-tab=sourcing]")
        page.select_option("#filter-student-bachelor-year", "2019")
        page.wait_for_function("document.getElementById('edu-results').innerText.includes('Test Pool Person From Apify')", timeout=15000)

        # Clean layout: no source dropdown / depth selector / old header, stats or buttons.
        check(page.get_attribute("#filter-student-source", "type") == "hidden"
              and page.get_attribute("#filter-student-source", "value") == "apify", "data source should be a fixed hidden 'apify'")
        check(page.get_attribute("#filter-student-depth", "type") == "hidden"
              and page.get_attribute("#filter-student-depth", "value") == "6", "depth should be fixed at 6 pages (150 profiles)")
        for gone in ("#btn-open-live-xray", "#btn-open-live-linkedin", "#btn-open-quick-import", "#btn-export-students-csv",
                     "#stat-students-onboarded", "#filter-student-pdl-size", ".btn-preset"):
            check(page.query_selector(gone) is None, f"{gone} should be removed from Sourcing")
        pane = page.inner_text("#tab-students")
        for gone_text in ("US Bench Sourcing", "Talent Sourcing Pool", "Experienced Tech Candidates", "Google X-Ray", "Data source", "Search depth"):
            check(gone_text.lower() not in pane.lower(), f"Sourcing still shows {gone_text!r}")
        check("$1.20" in page.inner_text("#btn-apply-student-filter"), "search button should state its cost")
        for width in (1280, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            tops = page.evaluate("""() => [...document.querySelectorAll('#form-edu-search > *')]
                .filter(el => el.offsetParent !== null).map(el => Math.round(el.getBoundingClientRect().bottom))""")
            check(len(set(tops)) == 1, f"at {width}px the Passout controls should fit one row (bottoms: {tops})")
        page.set_viewport_size({"width": 1280, "height": 720})
        check(page.is_visible("#btn-apply-student-filter"), "Search LinkedIn should be on the Passout tab's row")

        table = page.inner_text("#edu-results")
        check("Test Pool Person From PDL" in table, "person found earlier by PDL is missing from the pool")
        check("Test Pool Person From Apify" in table, "Apify pool person missing")

        body = page.inner_text("body")
        check("People Data Labs" not in body, "page text still mentions People Data Labs")
        status = page.inner_text("#students-search-status") if page.is_visible("#students-search-status") else ""
        check("People Data Labs" not in status, f"status line mentions PDL: {status}")

        page.screenshot(path=os.path.join(tmp_dir, "sourcing.png"), full_page=False)

        # Clicking Search sends year 2019 with the fixed depth. The request is aborted by the route
        # above, so it never reaches the server (and never Apify).
        with page.expect_request("**/api/students/search-start**") as req_info:
            page.click("#btn-apply-student-filter")
        sent = req_info.value.post_data_json or {}
        check(sent.get("bachelor_year") == "2019" and int(sent.get("pages") or 0) == 1,
              f"search request should ask for 2019, one page at a time: {sent}")
        page.wait_for_timeout(800)   # let the route handler record the aborted request
        blocked_search = [u for u in blocked if "search-start" in u]
        check(len(blocked_search) >= 1, "the search-start request should have been intercepted")
        blocked[:] = [u for u in blocked if "search-start" not in u]   # expected, intercepted on purpose
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not blocked, f"a paid Apify call was attempted (blocked): {blocked}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", os.path.join(tmp_dir, "sourcing.png"))
    sys.exit(1)
print(f"PASS: Sourcing tab in Edge - clean layout (no old header/stats/buttons/source/depth), fixed-depth search request, pool incl. PDL-found people, no JS errors, no paid calls. Screenshot: {os.path.join(tmp_dir, 'sourcing.png')}")
