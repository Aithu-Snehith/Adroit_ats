"""Browser test (Playwright + Microsoft Edge): Resume Optimizer progress + timings.

While the optimizer runs, the button shows a live "Optimizing... Ns" timer and a status line says
what happens in order; when it finishes, the result shows how long each step took ("Took 4.3s ·
AI analysis 2.1s · AI rewrite 2.1s · Word file 0.0s"), and a second run of the same resume + JD
shows the analysis as reused. Gemini is never called: resume_bot._generate is a stub that waits
about 2 s per step. Labelled test fixture.
Run: python test_optimizer_progress_browser.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_opt_progress.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.GEMINI_API_KEY = "test-key-not-real"
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

RESUME = """RAVI PROGRESSTEST
Senior Data Engineer
PROFESSIONAL SUMMARY
Data Engineer with 8 years of experience building pipelines on AWS using Spark, Python and SQL.
TECHNICAL SKILLS
Big Data: Spark, Hadoop, Hive, Airflow
EXPERIENCE
Example Bank Testco | Data Engineer | Jan 2022 - Present
Built batch pipelines with Spark and Python on AWS EMR."""
ANALYSIS = {"initial_match_percentage": 74, "match_breakdown": {"mandatory_skills": 30, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 7, "certifications_education": 5, "location_work_authorization": 2},
            "domain_detected": "Banking", "strong_match_skills": ["Spark"], "partial_match_skills": [], "mandatory_missing_skills": ["Kafka"],
            "preferred_missing_skills": [], "risky_skills_avoided": [], "skills_to_add": {"summary": ["Kafka"], "technical_skills": ["Kafka"], "projects": [], "environment": []},
            "ats_optimization_notes": ["Keywords optimized: Kafka"], "target_match_percentage": 85}
calls = []


def slow_generate(client, types, system, prompt, want_json, max_tokens):
    calls.append("analysis" if want_json else "rewrite")
    time.sleep(2.0)
    if want_json:
        return json.dumps(ANALYSIS), False, "stub-model"
    return "[3] Data Engineer with 8 years of experience building pipelines on AWS using Spark, Kafka, Python and SQL.\n[5] Big Data: Spark, Kafka, Hadoop, Hive, Airflow", False, "stub-model"


rb._generate = slow_generate
rb.gemini_configured = lambda: True

EMAIL, PASSWORD = "optprog@example.invalid", "OptProg-Test-1"
models.create_user("Opt Progress Admin", EMAIL, PASSWORD, role="Admin")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "opt_progress.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.71.0" in page.content(), "cache-buster not bumped to 5.71.0")
        page.click("a.nav-item[data-tab=resumebot]")
        page.fill("#resumebot-resume-text", RESUME)
        page.fill("#resumebot-jd-text", "Senior Data Engineer. Requirements: Spark, Python, Kafka, 7+ years.")
        page.click("#btn-run-resume-optimization")
        page.wait_for_function("document.getElementById('btn-run-resume-optimization').innerText.includes('Optimizing... 2s')", timeout=10000)
        check(page.is_visible("#resumebot-progress") and "writes only the changes" in page.inner_text("#resumebot-progress"), "status line while running")
        check(page.is_disabled("#btn-run-resume-optimization"), "button disabled while running")
        page.wait_for_selector(".optimizer-timings", timeout=30000)
        t = page.inner_text(".optimizer-timings")
        check(t.startswith("Took ") and "AI analysis 2." in t and "AI rewrite 2." in t and "Word file" in t, f"timings line: {t!r}")
        check(not page.is_visible("#resumebot-progress") and "Optimize & Calculate" in page.inner_text("#btn-run-resume-optimization"), "progress cleared, button restored")
        page.screenshot(path=shot)

        # Re-optimize the same resume + JD: analysis reused, one AI call
        calls.clear()
        page.click("#btn-run-resume-optimization")
        page.wait_for_function("document.querySelector('.optimizer-timings') && document.querySelector('.optimizer-timings').innerText.includes('reused')", timeout=30000)
        check(calls == ["rewrite"], f"re-optimize made one AI call: {calls}")
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
print(f"PASS: Resume Optimizer in Edge - live timer + status while running, per-step timings after, re-optimize reuses the analysis. Screenshot: {shot}")
