"""Browser test (Playwright + Microsoft Edge): cloud alignment on screen.

Resume Optimizer: an AWS resume optimized for an Azure JD shows "Cloud mismatch" with which sections
lack Azure and the equivalent services as talking points to confirm; an Azure-ready resume shows
"Cloud check passed". Paste Requirement & Draft: pasting an Azure JD for a consultant whose profile
shows AWS shows the JD cloud + a warning. Gemini is never called (stub). Labelled test fixtures.
Run: python test_cloud_align_browser.py
"""
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_cloud_ui.db")
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
ANALYSIS = {"initial_match_percentage": 70, "match_breakdown": {"mandatory_skills": 28, "recent_project_relevance": 17, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Banking",
            "strong_match_skills": ["Spark"], "partial_match_skills": [], "mandatory_missing_skills": [], "preferred_missing_skills": [],
            "risky_skills_avoided": [], "skills_to_add": {"summary": [], "technical_skills": [], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 72}
REWRITE = {"answer": "[2] Data Engineer with 8 years building batch pipelines on AWS using Spark and Python."}
rb._generate = lambda c, t, s, p, want_json, max_tokens: (json.dumps(ANALYSIS), False, "stub") if want_json else (REWRITE["answer"], False, "stub")
rb.gemini_configured = lambda: True

AWS_RESUME = """RAVI CLOUDUI
PROFESSIONAL SUMMARY
Data Engineer with 8 years building pipelines on AWS using Spark and Python.
TECHNICAL SKILLS
Cloud: AWS (S3, EMR, Glue, Redshift)
PROFESSIONAL EXPERIENCE
Example Bank Testco | Data Engineer | Jan 2022 - Present
Built batch pipelines with Spark on AWS EMR and S3."""
AZURE_JD = "Senior Data Engineer\nMust have 5+ years with Azure Data Factory and Synapse.\nHands-on AKS required.\nAWS is a plus."

EMAIL, PASSWORD = "cloudui@example.invalid", "CloudUi-Test-1"
admin = models.create_user("Cloud UI Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
cid = models.create_candidate("Ravi Cloudui", "ravi-cloudui@example.invalid", title="Data Engineer",
                              primary_skills="Spark, Python, AWS, S3, EMR", assigned_user_id=admin_id)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shots = []


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
        check("app.js?v=5.83.0" in page.content(), "cache-buster not bumped to 5.83.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)

        # ---- Resume Optimizer: mismatch panel
        page.click("a.nav-item[data-tab=resumebot]")
        page.fill("#resumebot-resume-text", AWS_RESUME)
        page.fill("#resumebot-jd-text", AZURE_JD)
        page.click("#btn-run-resume-optimization")
        page.wait_for_selector("#result-cloud-check", state="visible", timeout=30000)
        txt = page.inner_text("#result-cloud-check")
        check("Cloud mismatch" in txt and "primary cloud is Azure" in txt and "do not relabel AWS work as Azure" in txt, f"mismatch panel: {txt!r}")
        check("✗ summary" in txt and "✗ skills" in txt and "✗ recent project" in txt, "which sections lack Azure")
        check("EC2 → Azure Virtual Machines" in txt and "only if they really used them" in txt, "equivalents as talking points")
        page.locator("#result-cloud-check").scroll_into_view_if_needed()
        shots.append(os.path.join(tmp_dir, "cloud_mismatch.png"))
        page.screenshot(path=shots[-1])

        # an Azure-ready resume passes
        REWRITE["answer"] = ""   # the AI changes nothing this time - the check runs on the resume as given
        page.fill("#resumebot-resume-text", AWS_RESUME.replace("on AWS using", "on Azure and AWS using").replace("Cloud: AWS", "Cloud: Azure (ADF, Synapse), AWS")
                  .replace("on AWS EMR and S3.", "on AWS EMR and S3, orchestrated with Azure Data Factory."))
        page.click("#btn-run-resume-optimization")
        page.wait_for_function("document.getElementById('result-cloud-check').innerText.includes('Cloud check passed')", timeout=30000)

        # ---- Paste Requirement & Draft: JD cloud + warning for an AWS consultant
        page.click("#btn-open-paste-draft-modal")
        page.wait_for_selector("#modal-paste-draft", state="visible")
        page.select_option("#pd-consultant-select", str(cid))
        page.fill("#pd-raw-text", AZURE_JD)
        page.wait_for_selector("#pd-cloud-note", state="visible", timeout=10000)
        note = page.inner_text("#pd-cloud-note")
        check("JD cloud: Azure" in note and "Ravi Cloudui's profile shows AWS" in note and "confirm their Azure experience" in note, f"paste note: {note!r}")
        shots.append(os.path.join(tmp_dir, "cloud_paste.png"))
        page.screenshot(path=shots[-1])
        page.click("#btn-cancel-paste-draft")
        page.click("#btn-open-paste-draft-modal")
        check(not page.is_visible("#pd-cloud-note"), "note cleared when the modal reopens")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: cloud alignment in Edge - optimizer mismatch/pass panel with equivalents, Paste & Draft JD cloud warning. Screenshots: {shots}")
