"""Browser test (Playwright + Microsoft Edge): the Resume Optimizer must always show the resume of
the consultant selected in the dropdown.

Root cause this guards against: loading a consultant's resume is asynchronous. When the previously
selected consultant's load was slow (e.g. Sai Teja's PDF read for the first time) and the recruiter
selected another consultant (Praveen), the slow load finished LAST and replaced Praveen's resume -
the dropdown said "Praveen" while the box (and an Optimize click) used Sai Teja's resume.

The server delays Sai Teja's resume by 2.5 s to recreate that timing. No AI call is ever made: the
optimizer endpoint is blocked (requests are recorded, then aborted). All people are labelled test fixtures.
Run: python test_resume_select_race_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_race.db")
os.environ["DATABASE_URL"] = ""
for k in ("APIFY_API_TOKEN", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY"):
    os.environ[k] = ""
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

EMAIL, PASSWORD = "race-admin@example.invalid", "Race-Test-1"
admin = models.create_user("Race Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
sai_id = models.create_candidate("Saiteja Racetest", "sai@example.invalid", title="DevOps Engineer", assigned_user_id=admin_id,
                                 resume_text="SAITEJA RACE RESUME (test fixture) - DevOps, Kubernetes, Terraform, Azure.")
pra_id = models.create_candidate("Praveen Racetest", "pra@example.invalid", title="AI Engineer", assigned_user_id=admin_id,
                                 resume_text="PRAVEEN RACE RESUME (test fixture) - AI automation, Python, LangChain, n8n.")
job_id = models.save_or_update_scraped_job({
    "title": "AI Engineer Race Test", "company": "Race Co", "source": "Manual Paste", "url": "",
    "description": "Full pasted requirement for the race test: AI engineer with Python and LangChain. " * 8,
    "matched_skills": "AI Engineer", "match_score": 95})
job_id = job_id["id"] if isinstance(job_id, dict) else job_id


@app_module.app.before_request
def _slow_saiteja():
    if request.path == f"/api/consultants/{sai_id}":
        time.sleep(2.5)


with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"

failures, js_errors, optimize_calls = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def guard(route):
    if "/api/resume-bot/optimize" in route.request.url:
        optimize_calls.append(route.request.post_data_json or {})
        route.abort()
    else:
        route.continue_()


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/*", guard)
        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.67.0" in page.content(), "cache-buster not bumped to 5.67.0")

        # 1. open the optimizer on Saiteja (slow), then immediately pick Praveen
        page.click("a.nav-item[data-tab=resumebot]")
        page.select_option("#resumebot-consultant-select", str(sai_id))
        page.wait_for_timeout(200)
        page.select_option("#resumebot-consultant-select", str(pra_id))
        page.wait_for_timeout(200)
        # while Saiteja's slow load is still in flight, Optimize must not send anything
        page.click("#btn-run-resume-optimization")
        page.wait_for_timeout(3500)   # Saiteja's slow load has now finished
        box = page.input_value("#resumebot-resume-text")
        check("PRAVEEN RACE RESUME" in box and "SAITEJA" not in box, f"dropdown says Praveen but the box holds: {box[:80]!r}")
        note = page.inner_text("#resumebot-source-note")
        check("Praveen Racetest" in note and "Saiteja" not in note, f"source note names the wrong person: {note!r}")

        check(not optimize_calls, f"Optimize sent a request while the selected consultant's resume was not loaded: {optimize_calls}")

        # 2. Jobs -> Optimize Resume for Praveen while the optimizer tab was last on Saiteja: the in-place
        #    panel uses the ROW's Target Candidate - Praveen's resume shown, Praveen's id sent
        page.select_option("#resumebot-consultant-select", str(sai_id))
        page.wait_for_function("document.getElementById('resumebot-resume-text').value.includes('SAITEJA')", timeout=8000)
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_selector(f".job-row[data-job-id='{job_id}']", timeout=45000)
        page.select_option(f".job-row[data-job-id='{job_id}'] .job-consultant-select", str(pra_id))
        page.click(f".job-row[data-job-id='{job_id}'] .btn-optimize-job")
        page.wait_for_selector("#modal-draft-optimize", state="visible", timeout=5000)
        page.wait_for_function("document.getElementById('do-original').value.includes('RACE RESUME')", timeout=10000)
        box = page.input_value("#do-original")
        check("PRAVEEN RACE RESUME" in box and "SAITEJA" not in box, f"Jobs -> Optimize panel shows {box[:80]!r}")
        page.wait_for_timeout(500)
        sent = [c.get("candidate_id") for c in optimize_calls]
        check(sent == [pra_id], f"the panel's optimize request is for Praveen ({pra_id}): {sent}")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: the optimizer always shows (and would send) the selected consultant's resume, even when an earlier load is slow.")
