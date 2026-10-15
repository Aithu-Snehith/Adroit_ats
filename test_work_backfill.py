"""Fill Open to work / job type for profiles saved before the app kept them (work_backfill.py).

Reads the stored results of past searches (Apify datasets, newest first, + HarvestAPI items kept
locally) and fills ONLY profiles with no work data: a newer search's value is never overwritten by an
older one, a profile that already has data is untouched, expired searches are counted, nothing new
is scraped (only GET requests - no actor run). Admin only; the admin sees a button on the Sourcing
note and the numbers update. Apify is a stub - no network, no cost. Labelled test fixtures.
Run: python test_work_backfill.py
"""
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_backfill.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
import work_backfill  # noqa: E402

failures, calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
ADMIN_EMAIL, ADMIN_PW = "backfill-admin@example.invalid", "Backfill-1"
ADMIN = {"id": uid(models.create_user("Backfill Admin", ADMIN_EMAIL, ADMIN_PW, role="Admin")), "name": "Backfill Admin", "role": "Admin"}
REC = {"id": uid(models.create_user("Backfill Rec", "backfill-rec@example.invalid", "Backfill-2", role="Recruiter")), "name": "Backfill Rec", "role": "Recruiter"}
URL = "https://www.linkedin.com/in/{}.example.invalid"


def old_item(slug, first):     # scraped before the work fields were kept
    return {"linkedinUrl": URL.format(slug), "firstName": first, "lastName": "Backfilltest", "headline": "Engineer",
            "location": {"linkedinText": "Dallas, Texas"}, "currentPosition": [{"companyName": "Co"}],
            "education": [{"schoolName": "JNTUH", "degree": "B.Tech", "endDate": {"year": 2019}},
                          {"schoolName": "University of Texas at Dallas", "degree": "MS", "endDate": {"year": 2022}}]}


def work(slug, otw, emp=None, wp=None):
    return {"linkedinUrl": URL.format(slug), "openToWork": otw, "currentPosition": [{"companyName": "Co"}],
            "experience": [{"companyName": "Co", "employmentType": emp, "workplaceType": wp, "endDate": {"text": "Present"}}]}


li.ingest_profiles(li.ApifyProfileProvider().to_profiles([old_item(s, n) for s, n in
                                                          (("bf-a", "Asha"), ("bf-b", "Balu"), ("bf-d", "Dinesh"), ("bf-e", "Ela"))]), "apify", None)
li.ingest_profiles(li.ApifyProfileProvider().to_profiles([dict(old_item("bf-c", "Chetan"), **work("bf-c", True, "Contract"))]), "apify", None)
conn = models.get_db_connection()
conn.cursor().execute("INSERT INTO harvest_runs (status, start_page, params_json, cost_usd, items_json) VALUES ('SUCCEEDED', 1, '{}', 0, ?)",
                      (json.dumps([work("bf-d", False, "Full-time", "On-site")]),))
conn.commit()
conn.close()

DATASETS = {   # run order newest first: run-1 (A open to work), run-2 expired, run-3 (older: A, B, C)
    "ds-1": [work("bf-a", True, "Contract", "Remote")],
    "ds-3": [work("bf-a", False, "Full-time", "On-site"), work("bf-b", False, "Full-time", "Hybrid"), work("bf-c", False, "Full-time")],
}


class Resp:
    def __init__(self, code, data):
        self.status_code, self._data = code, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeRequests:
    @staticmethod
    def get(url, params=None, timeout=None):
        calls.append(("GET", url, dict(params or {})))
        if url.endswith("/runs"):
            return Resp(200, {"data": {"items": [{"id": "run-1", "defaultDatasetId": "ds-1"}, {"id": "run-2", "defaultDatasetId": "ds-2"},
                                                 {"id": "run-3", "defaultDatasetId": "ds-3"}]}})
        ds = url.split("/datasets/")[1].split("/")[0]
        return Resp(200, DATASETS[ds]) if ds in DATASETS else Resp(404, {"error": {"type": "record-not-found"}})

    @staticmethod
    def post(*a, **k):
        calls.append(("POST", a, k))
        raise AssertionError("no new scraping allowed")


work_backfill.requests = FakeRequests
config.APIFY_API_TOKEN = "apify-test-token-not-real"

client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {**REC, "email": "backfill-rec@example.invalid"}
check(client.post("/api/admin/sourcing/fill-work-info").status_code == 403, "recruiters can't run it")
with client.session_transaction() as s:
    s["user"] = {**ADMIN, "email": ADMIN_EMAIL}
r = client.post("/api/admin/sourcing/fill-work-info")
st = r.get_json()
check(r.status_code == 200, f"admin runs it: {r.status_code} {st}")
check(st["missing_before"] == 4 and st["filled"] == 3 and st["still_missing"] == 1, f"fills A, B, D; E has nothing anywhere: {st}")
check(st["searches_read"] == 2 and st["searches_expired"] == 1, f"expired search counted: {st}")
check(not [c for c in calls if c[0] != "GET"], "only reads - no new scraping")
ds_calls = [c for c in calls if "/datasets/" in c[1]]
check(ds_calls and all(c[2].get("fields") == work_backfill.FIELDS for c in ds_calls), "reads only the needed fields")

conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT linkedin_url, open_to_work, current_employment_type, current_workplace_type FROM linkedin_profiles")
got = {r["linkedin_url"].split("/in/")[1].split(".")[0]: (r["open_to_work"], r["current_employment_type"], r["current_workplace_type"]) for r in cur.fetchall()}
conn.close()
check(got["bf-a"] == (1, "Contract", "Remote"), f"newest search wins for A: {got['bf-a']}")
check(got["bf-b"] == (0, "Full-time", "Hybrid"), f"B filled from an older search: {got['bf-b']}")
check(got["bf-c"] == (1, "Contract", None), f"C already had data - untouched: {got['bf-c']}")
check(got["bf-d"] == (0, "Full-time", "On-site"), f"D filled from the HarvestAPI items: {got['bf-d']}")
check(got["bf-e"] == (None, None, None), "E stays blank - nothing guessed")
check(client.post("/api/admin/sourcing/fill-work-info").get_json()["filled"] == 0, "running again changes nothing")

# ---- the button (admin, Edge)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

conn = models.get_db_connection()
conn.cursor().execute("UPDATE linkedin_profiles SET open_to_work = NULL, current_employment_type = NULL, current_workplace_type = NULL WHERE linkedin_url LIKE '%bf-a%'")
conn.commit()
conn.close()
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", ADMIN_EMAIL)
        page.fill("input[name=password]", ADMIN_PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.86.0" in page.content(), "cache-buster not bumped to 5.86.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=ALL]")
        page.wait_for_selector("#btn-fill-work-info", timeout=15000)
        check(page.inner_text("#edu-st-otw") == "1", f"before: 1 open to work ({page.inner_text('#edu-st-otw')})")
        page.click("#btn-fill-work-info")
        page.wait_for_function("document.body.innerText.includes('Filled 1 profile')", timeout=20000)
        page.wait_for_function("document.getElementById('edu-st-otw').textContent === '2'", timeout=10000)
        browser.close()
finally:
    server.shutdown()
check(not js_errors, f"JavaScript errors: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: fill work info from past searches - only empty profiles, newest search wins, expired counted, HarvestAPI items, read-only, admin only, button in Edge updates the numbers.")
