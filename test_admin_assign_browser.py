"""Admin assigns consultants to recruiters (Consultants tab).

1. API: only an admin can assign; a recruiter on the other team (USA / India) is refused with the reason;
   'already assigned' is refused; after the move the new recruiter sees the consultant and the old one
   doesn't; bulk assign moves the ones that can move and lists the rest with the reason; logged.
2. Edge as admin: '✎ Change' in the Assigned Recruiter column (recruiters listed with their team), tick
   several + Assign. Edge as a recruiter: no Change buttons, no tick boxes.
Labelled test fixtures. Run: python test_admin_assign_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_assign.db")
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
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
ADMIN_EMAIL, ADMIN_PW = "assign-admin@example.invalid", "Assign-Admin-1"
ADMIN = uid(models.create_user("Ada Assigntest", ADMIN_EMAIL, ADMIN_PW, role="Admin"))
SNEHIT = uid(models.create_user("Snehit Assigntest", "snehit-assign@example.invalid", "Assign-2", role="Recruiter", region="USA"))
RAVI_EMAIL, RAVI_PW = "ravi-assign@example.invalid", "Assign-3"
RAVI = uid(models.create_user("Ravi Assigntest", RAVI_EMAIL, RAVI_PW, role="Recruiter", region="USA"))
INDIRA = uid(models.create_user("Indira Assigntest", "indira-assign@example.invalid", "Assign-4", role="Recruiter", region="India"))
visakh = models.create_candidate("Visakh Assigntest", "visakh-assign@example.invalid", title="DevOps Engineer", assigned_user_id=RAVI)
mani = models.create_candidate("Mani Assigntest", "mani-assign@example.invalid", title="Java Developer", assigned_user_id=RAVI)
priya = models.create_candidate("Priya Assigntest", "priya-assign@example.invalid", title="Data Engineer", assigned_user_id=INDIRA, country="India")

client = app_module.app.test_client()


def as_user(uid_, name, role):
    with client.session_transaction() as s_:
        s_["user"] = {"id": uid_, "name": name, "role": role, "email": f"{name}@x"}


def visible(uid_, name, role="Recruiter"):
    as_user(uid_, name, role)
    return {c["name"] for c in client.get("/api/consultants").get_json()}


# ---- 1. API
as_user(RAVI, "Ravi", "Recruiter")
check(client.post(f"/api/admin/candidates/{visakh}/reassign", json={"assigned_user_id": SNEHIT}).status_code == 403, "a recruiter can't assign")
check(client.post("/api/admin/candidates/reassign", json={"candidate_ids": [visakh], "assigned_user_id": SNEHIT}).status_code == 403, "or bulk assign")
as_user(ADMIN, "Ada", "Admin")
r = client.post(f"/api/admin/candidates/{visakh}/reassign", json={"assigned_user_id": INDIRA})
check(r.status_code == 400 and "India team" in r.get_json()["error"] and "USA consultant" in r.get_json()["error"], f"other team refused: {r.get_json()}")
r = client.post(f"/api/admin/candidates/{visakh}/reassign", json={"assigned_user_id": RAVI})
check(r.status_code == 400 and "already assigned" in r.get_json()["error"], "already assigned refused")
r = client.post(f"/api/admin/candidates/{visakh}/reassign", json={"assigned_user_id": SNEHIT})
check(r.status_code == 200 and "Snehit" in r.get_json()["message"], f"assigned to Snehit: {r.get_json()}")
check("Visakh Assigntest" in visible(SNEHIT, "Snehit") and "Visakh Assigntest" not in visible(RAVI, "Ravi"), "Snehit sees Visakh now, Ravi doesn't")
logs = models.get_activity_logs(50) if hasattr(models, "get_activity_logs") else []
check(not logs or any("from Ravi Assigntest to Snehit Assigntest" in (l.get("details") or "") for l in logs), "move logged with both names")
as_user(ADMIN, "Ada", "Admin")
r = client.post("/api/admin/candidates/reassign", json={"candidate_ids": [mani, priya, visakh], "assigned_user_id": SNEHIT}).get_json()
check([m["name"] for m in r["moved"]] == ["Mani Assigntest"], f"bulk moved the one that can move: {r}")
reasons = {s["name"]: s["reason"] for s in r["skipped"]}
check("India consultant" in reasons.get("Priya Assigntest", "") and "already assigned" in reasons.get("Visakh Assigntest", ""), f"skipped with reasons: {reasons}")
models.assign_candidate_to_recruiter(mani, RAVI)          # back, for the screen part
models.assign_candidate_to_recruiter(visakh, RAVI)

# ---- 2. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "assign.png")


def login(page, email, pw):
    page.goto(f"http://127.0.0.1:{port}/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", pw)
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")
    page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)
    page.click("a.nav-item[data-tab=candidates]")
    page.wait_for_selector("#consultants-table-body tr", timeout=10000)


ROW = "#consultants-table-body tr:has-text('{}')"
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        login(page, ADMIN_EMAIL, ADMIN_PW)
        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to 5.88.0")
        check(page.locator("#consultants-table-body .btn-trigger-reassign").count() >= 3, "admin sees ✎ Change on every consultant")
        page.click(ROW.format("Visakh Assigntest") + " .btn-trigger-reassign")
        page.wait_for_function("document.getElementById('modal-reassign-consultant').style.display !== 'none'", timeout=5000)
        page.wait_for_function("document.getElementById('reassign-target-user-id').options.length >= 2", timeout=5000)
        opts = page.eval_on_selector_all("#reassign-target-user-id option", "os => os.map(o => o.textContent)")
        check("Snehit Assigntest - USA team" in opts and "Indira Assigntest - India team" in opts and not any(o.startswith("Ravi") for o in opts),
              f"recruiters with their team, current one left out: {opts}")
        page.select_option("#reassign-target-user-id", str(SNEHIT))
        page.click("#btn-submit-reassign")
        page.wait_for_function("[...document.querySelectorAll('#consultants-table-body tr')].some(tr => tr.innerText.includes('Visakh Assigntest') && tr.innerText.includes('Snehit Assigntest'))", timeout=10000)
        # bulk: Mani (USA) + Priya (India) to Snehit -> Mani moves, Priya is refused with the reason
        page.check(ROW.format("Mani Assigntest") + " .cons-select")
        page.check(ROW.format("Priya Assigntest") + " .cons-select")
        page.wait_for_function("document.getElementById('cons-bulk-count').innerText === '2 consultants selected'", timeout=5000)
        page.select_option("#cons-bulk-recruiter", str(SNEHIT))
        page.screenshot(path=shot)
        page.click("#cons-bulk-assign")
        page.wait_for_function("[...document.querySelectorAll('#consultants-table-body tr')].some(tr => tr.innerText.includes('Mani Assigntest') && tr.innerText.includes('Snehit Assigntest'))", timeout=10000)
        check("Indira Assigntest" in page.inner_text(ROW.format("Priya Assigntest")), "Priya stays with Indira (other team)")
        check(not page.is_visible("#cons-bulk-bar"), "selection cleared after assigning")
        page.wait_for_selector("text=Not moved", timeout=5000)

        page2 = browser.new_page(viewport={"width": 1500, "height": 950})
        page2.on("pageerror", lambda e: js_errors.append(str(e)))
        models.assign_candidate_to_recruiter(mani, RAVI)
        login(page2, RAVI_EMAIL, RAVI_PW)
        check(page2.locator(".btn-trigger-reassign").count() == 0 and page2.locator(".cons-select").count() == 0
              and page2.locator("#cons-bulk-bar").count() == 0, "a recruiter has no assign controls")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: admin assigns consultants - admin only, team check, visibility follows, bulk with reasons, Change + bulk in Edge, nothing for recruiters. Screenshot: {shot}")
