"""Browser test (Playwright + Microsoft Edge): Sourcing number cards + College / Year / Location
checkbox dropdowns (saved profiles only).

The five cards show the numbers; each dropdown opens a checkbox list with counts (College has a
search box); ticking narrows the table and the Match card, the button shows what is picked, chips
appear with x and Clear all; clicking outside closes the list; Search LinkedIn (paid) is untouched.
No network beyond the local app. People are labelled test fixtures.
Run: python test_sourcing_facets_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_facets_ui.db")
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

EMAIL, PASSWORD = "facet-ui@example.invalid", "FacetUi-Test-1"
models.create_user("Kavya Facetui", EMAIL, PASSWORD, role="Recruiter")


def item(slug, first, college, year, loc):
    return {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Facetui",
            "headline": "Engineer", "location": {"linkedinText": loc}, "currentPosition": [], "openToWork": None, "experience": [],
            "education": [{"schoolName": college, "degree": "B.Tech", "endDate": {"year": year}},
                          {"schoolName": "University of Texas at Dallas", "degree": "MS", "endDate": {"year": year + 3}}]}


li.ingest_profiles(li.ApifyProfileProvider().to_profiles([
    item("fu-1", "Arjun", "JNTUH", 2019, "Dallas, Texas"), item("fu-2", "Bhavya", "JNTUH", 2022, "San Jose, California"),
    item("fu-3", "Charvi", "CBIT", 2022, "Austin, TX")]), "apify", None)
li.ingest_profiles(li.ApifyProfileProvider().to_profiles([{   # no US Master's: only on "All saved profiles"
    "linkedinUrl": "https://www.linkedin.com/in/fu-4.example.invalid", "firstName": "Dev", "lastName": "Facetui", "headline": "QA",
    "location": {"linkedinText": "Houston, Texas"}, "currentPosition": [], "openToWork": None, "experience": [],
    "education": [{"schoolName": "JNTUH", "degree": "B.Tech", "endDate": {"year": 2012}}]}]), "apify", None)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "facets.png")
ROWS = "document.querySelectorAll('#edu-results tr[data-pid]').length"


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
        check("app.js?v=5.76.0" in page.content(), "cache-buster not bumped to 5.76.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.select_option("#filter-student-bachelor-year", "All")
        page.wait_for_function(f"{ROWS} === 3", timeout=15000)
        check(page.inner_text("#edu-st-total") == "4" and page.inner_text("#edu-st-match") == "3", "cards: 4 saved, 3 on the Passout tab")
        check(page.is_visible("#btn-apply-student-filter"), "paid Search LinkedIn button still there")

        # College dropdown: search box + counts
        page.click(".edu-dd-btn[data-facet=colleges]")
        panel = ".edu-dd-panel[data-facet=colleges]"
        page.wait_for_selector(f"{panel} .edu-dd-opt")
        check(page.locator(f"{panel} .edu-dd-opt").count() == 2, "two colleges listed")
        check(page.locator(f"{panel} .edu-dd-search").count() == 1, "college search box")
        page.fill(f"{panel} .edu-dd-search", "chaitanya")
        page.wait_for_function(f"document.querySelectorAll('{panel} .edu-dd-opt').length === 1")
        page.fill(f"{panel} .edu-dd-search", "")
        page.wait_for_function(f"document.querySelectorAll('{panel} .edu-dd-opt').length === 2")
        jntu = page.locator(f"{panel} label", has_text="Jawaharlal")
        check("2" in jntu.inner_text(), f"count beside the college: {jntu.inner_text()!r}")
        jntu.locator("input").check()
        page.wait_for_function(f"{ROWS} === 2", timeout=10000)
        check(page.is_visible(panel), "list stays open while ticking")
        check(page.inner_text("#edu-st-match") == "2", "Match card follows")
        check("Jawaharlal" in page.inner_text(".edu-dd-btn[data-facet=colleges]"), "button shows the pick")
        page.screenshot(path=shot)

        # click outside closes; then Year dropdown
        page.click("#edu-stats")
        check(not page.is_visible(panel), "click outside closes the list")
        page.click(".edu-dd-btn[data-facet=years]")
        page.locator(".edu-dd-panel[data-facet=years] label", has_text="2022").locator("input").check()
        page.wait_for_function(f"{ROWS} === 1", timeout=10000)
        check("Bhavya" in page.inner_text("#edu-results"), "college + year -> Bhavya")
        chips = page.inner_text("#edu-facet-chips")
        check("2022" in chips and "Jawaharlal" in chips and "Clear all" in chips, f"chips: {chips!r}")

        # chip x removes; Clear all resets
        page.click("#edu-stats")   # close the open list first (it sits over the chips)
        page.click("#edu-facet-chips .edu-chip-x[data-value='2022']")
        page.wait_for_function(f"{ROWS} === 2", timeout=10000)
        page.click("#edu-facet-clear-all")
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        check(not page.is_visible("#edu-facet-chips"), "chips hidden after Clear all")

        # Location: TX read as Texas
        page.click(".edu-dd-btn[data-facet=states]")
        page.locator(".edu-dd-panel[data-facet=states] label", has_text="Texas").locator("input").check()
        page.wait_for_function(f"{ROWS} === 2", timeout=10000)
        check(sorted(page.eval_on_selector_all("#edu-results tr[data-pid] td:first-child a", "els => els.map(e => e.textContent.trim().split(' ')[0])")) == ["Arjun", "Charvi"], "Texas")
        # All saved profiles: every one of the 4, paid search hidden
        page.click("#edu-facet-clear-all") if page.is_visible("#edu-facet-clear-all") else None
        page.click(".edu-tab[data-filter=ALL]")
        page.wait_for_function(f"{ROWS} === 4", timeout=10000)
        check(page.inner_text("#edu-st-match") == "4" and "Dev" in page.inner_text("#edu-results"), "All tab shows every saved profile")
        check(not page.is_visible("#btn-apply-student-filter"), "paid search hidden on All saved profiles")
        page.click(".edu-tab[data-filter=P]")
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        check(page.is_visible("#btn-apply-student-filter"), "paid search back on Passout tab")
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
print(f"PASS: sourcing cards + College/Year/Location dropdowns in Edge - counts, search box, narrowing, chips, Clear all, outside click. Screenshot: {shot}")
