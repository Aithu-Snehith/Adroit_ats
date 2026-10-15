"""Browser test (Playwright + Microsoft Edge): Sourcing on HarvestAPI direct (SOURCING_PROVIDER=harvestapi).

One click of Search LinkedIn starts exactly ONE page (the trial cap protects the $1 free credit, even
though the normal depth is 6 pages), the verified match appears, and the finished message shows
HarvestAPI's own numbers (profiles returned, LinkedIn's total, full profiles fetched) and the
estimated cost. HarvestAPI is never contacted (stubbed); Apify is never called. Labelled fixtures.
Run: python test_harvest_direct_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_harvest_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
os.environ["SOURCING_PROVIDER"] = "harvestapi"
os.environ["HARVESTAPI_API_KEY"] = "test-harvest-key-not-real"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
import app as app_module  # noqa: E402
import harvest_direct as hd  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PROFILES = {
    "https://www.linkedin.com/in/match-harvui": {
        "linkedinUrl": "https://www.linkedin.com/in/match-harvui", "firstName": "Ravi", "lastName": "Harvui",
        "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas, United States", "parsed": {"countryCode": "US"}},
        "education": [{"schoolName": "Osmania University", "degree": "Bachelor of Technology", "endDate": {"year": 2019}},
                      {"schoolName": "University of Texas at Dallas", "degree": "Master of Science", "fieldOfStudy": "Computer Science", "endDate": {"year": 2022}}],
        "currentPosition": [{"position": "Data Engineer", "companyName": "Test Co"}]},
    "https://www.linkedin.com/in/india-harvui": {
        "linkedinUrl": "https://www.linkedin.com/in/india-harvui", "firstName": "Sita", "lastName": "Harvui",
        "location": {"linkedinText": "Hyderabad, India", "parsed": {"countryCode": "IN"}},
        "education": [{"schoolName": "Osmania University", "degree": "Bachelor of Technology", "endDate": {"year": 2019}}]},
}
search_calls = []


class Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def fake_get(url, params=None, headers=None, timeout=None):
    if url.endswith("/linkedin/lead-search"):
        search_calls.append(dict(params))
        return Resp(200, {"elements": [{"linkedinUrl": u} for u in list(PROFILES) + ["https://www.linkedin.com/in/broken-harvui"]],
                          "pagination": {"totalElements": 1234}})
    p = PROFILES.get((params or {}).get("query"))
    return Resp(200, {"element": p}) if p else Resp(500, {"error": "profile unavailable"})


hd.requests.get = fake_get
EMAIL, PASSWORD = "harvui@example.invalid", "HarvUi-Test-1"
models.create_user("Harv UI Admin", EMAIL, PASSWORD, role="Admin")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors, apify_calls = [], [], []
shot = os.path.join(tmp_dir, "harvest_sourcing.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("request", lambda r: apify_calls.append(r.url) if "apify.com" in r.url else None)
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.86.0" in page.content(), "cache-buster not bumped to 5.86.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.select_option("#filter-student-bachelor-year", "2019")
        check(page.is_enabled("#btn-apply-student-filter"), "Search LinkedIn enabled with only a HarvestAPI key")
        page.click("#btn-apply-student-filter")
        page.wait_for_function("document.getElementById('students-search-status').innerText.includes('Finished')", timeout=60000)
        status = page.inner_text("#students-search-status")
        check(len(search_calls) == 1, f"one click = one HarvestAPI search page ({len(search_calls)})")
        check("HarvestAPI: the search returned 3 profiles (LinkedIn reports 1,234 in total), 2 full profiles fetched" in status, f"status: {status}")
        check("Estimated cost: about $0.11" in status and "HarvestAPI dashboard" in status, f"estimated cost: {status}")
        check("Apify cost" not in status, "no Apify wording on HarvestAPI")
        page.wait_for_function("document.body.innerText.includes('Ravi Harvui')", timeout=15000)
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not apify_calls, f"Apify was called: {apify_calls}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Sourcing on HarvestAPI in Edge - one page per click, match shown, HarvestAPI numbers + estimated cost. Screenshot: {shot}")
