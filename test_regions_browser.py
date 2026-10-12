"""Browser test (Playwright + Microsoft Edge): USA / India teams on screen.

Admin: a "Both teams / USA team / India team" switcher in the top bar; choosing India reloads with
only India consultants and the Jobs market locked to India; the recruiter roster has a Team column
that saves; Add Recruiter has a Team field. India recruiter: an "India team" badge (no switcher),
only India consultants, Jobs market locked to India. Scrapers stubbed. Labelled test fixtures.
Run: python test_regions_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_regions_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import regions  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
ADMIN = ("regui-admin@example.invalid", "RegUi-Test-1")
INREC = ("regui-in@example.invalid", "RegUi-Test-2")
admin_id = uid(models.create_user("Region UI Admin", ADMIN[0], ADMIN[1], role="Admin"))
in_id = uid(models.create_user("India Uirecruiter", INREC[0], INREC[1], role="Recruiter", region="India"))
us_id = uid(models.create_user("Usa Uirecruiter", "regui-us@example.invalid", "RegUi-Test-3", role="Recruiter", region="USA"))
c_in = models.create_candidate("Visakh Uiindia", "v-uiin@example.invalid", title="DevOps", assigned_user_id=in_id)
models.update_candidate(c_in, country="India")
c_us = models.create_candidate("Koushik Uiusa", "k-uius@example.invalid", title="Java Developer", assigned_user_id=us_id)
models.update_candidate(c_us, country="United States")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "regions.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


def login(page, cred):
    page.goto(f"http://127.0.0.1:{port}/login")
    page.fill("input[name=email]", cred[0])
    page.fill("input[name=password]", cred[1])
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")
    page.wait_for_function("typeof state !== 'undefined' && state.consultants", timeout=15000)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---- admin
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page, ADMIN)
        check("app.js?v=5.79.0" in page.content(), "cache-buster not bumped to 5.79.0")
        check(page.input_value("#view-region-switch") == "", "admin starts on Both")
        page.wait_for_function("state.consultants.length === 2", timeout=10000)
        check(not page.is_disabled("#filter-country"), "Both: market selectable")
        with page.expect_navigation():
            page.select_option("#view-region-switch", "India")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length === 1", timeout=15000)
        check(page.evaluate("state.consultants[0].name") == "Visakh Uiindia", "India view: India consultants only")
        check(page.input_value("#filter-country") == "India" and page.is_disabled("#filter-country"), "India view: jobs market locked to India")
        page.click("a.nav-item[data-tab=settings]")
        page.wait_for_selector(f".recruiter-region[data-id='{us_id}']", timeout=10000)
        check(page.input_value(f".recruiter-region[data-id='{in_id}']") == "India", "roster shows the team")
        page.select_option(f".recruiter-region[data-id='{us_id}']", "India")
        page.wait_for_timeout(600)
        conn = models.get_db_connection()
        check(regions.user_region(conn, us_id) == "India", "roster change saved")
        conn.close()
        page.evaluate("window.openAddRecruiterModal()")
        check(page.is_visible("#rec-region") and page.input_value("#rec-region") == "USA", "Add Recruiter has a Team field")
        page.screenshot(path=shot)
        page.close()

        # ---- India recruiter
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page, INREC)
        check(page.locator("#view-region-switch").count() == 0 and "India team" in page.inner_text("#team-badge"), "recruiter: India badge, no switcher")
        check([c for c in page.evaluate("state.consultants.map(c => c.name)")] == ["Visakh Uiindia"], "recruiter sees India consultants")
        check(page.input_value("#filter-country") == "India" and page.is_disabled("#filter-country"), "recruiter's jobs market locked to India")
        page.close()
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
print(f"PASS: teams in Edge - admin switcher (Both/India), team-only consultants, jobs market locked, roster Team saved, Add Recruiter Team, recruiter badge. Screenshot: {shot}")
