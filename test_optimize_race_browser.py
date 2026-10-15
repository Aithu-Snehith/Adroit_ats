"""Browser test (Playwright + Microsoft Edge): a slow optimize answer never lands in another job's window.

Reproduces the bug from the screenshot: optimizing job A (an AWS JD) takes a while; the window is closed
and job B (an Azure posting whose description couldn't be read) is opened; A's answer arrives late. It
must NOT fill B's window (status, optimized text, cloud check, Save) - B still asks for its JD and nothing
can be saved under B - and A's result is kept for A: reopening A shows it without running the AI again.
The AI is a slow stub - no network, no cost. Labelled test fixtures.
Run: python test_optimize_race_browser.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_race.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import docx  # noqa: E402
import app as app_module  # noqa: E402
import docx_editor  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import resume_versions  # noqa: E402

failures, ai_calls = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


RESUME = ["VISAKH RACETEST", "Senior Cloud DevOps Engineer with 10 years on AWS: EC2, VPC, IAM, Terraform.",
          "Built CI/CD pipelines with Jenkins and GitHub Actions."]
d = docx.Document()
for line in RESUME:
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
DOCX = buf.getvalue()
ANALYSIS = {"initial_match_percentage": 63, "match_breakdown": {"mandatory_skills": 25, "recent_project_relevance": 15, "domain_experience": 10,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2},
            "domain_detected": "Cloud", "strong_match_skills": ["AWS", "Terraform"], "partial_match_skills": [], "mandatory_missing_skills": [],
            "preferred_missing_skills": [], "risky_skills_avoided": [], "skills_to_add": {"summary": [], "technical_skills": [], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 85}


def slow_generate(client, types, system, prompt, want_json, max_tokens):
    if rb._ANALYSIS_CONTRACT in system:
        ai_calls.append("analysis")
        time.sleep(5)                       # the slow optimizer answer
        return json.dumps(ANALYSIS), False, "stub"
    if not want_json:
        ai_calls.append("rewrite")
        return "[2] Built CI/CD pipelines with Jenkins and GitHub Actions on AWS.", False, "stub"
    return json.dumps({}), False, "stub"   # email writing etc. - not part of this test


rb._generate = slow_generate
rb.ai_configured = lambda: True
rb.gemini_configured = lambda: False

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "race-rec@example.invalid", "Race-1"
REC = uid(models.create_user("Ravi Racetest", EMAIL, PW, role="Recruiter", region="India"))
cid = models.create_candidate("Visakh Racetest", "visakh.race@gmail.example", title="Senior Cloud DevOps Engineer",
                              primary_skills="AWS, Terraform, Jenkins", experience_years=10, assigned_user_id=REC)
models.update_candidate(cid, country="India", resume_text="\n".join(RESUME))
models.save_resume_file(cid, "Visakh_Resume.docx", DOCX)
JD_A = "Senior DevOps Engineer (AWS). Required: AWS EC2, VPC, IAM, Terraform, Jenkins CI/CD. " * 6
ja = models.save_or_update_scraped_job({"title": "DevOps Engineer AWS Racetest", "company": "Alpha", "source": "Manual Paste", "url": "",
                                        "recruiter_email": "a@alpha.example", "description": JD_A})
jb = models.save_or_update_scraped_job({"title": "Lead Engineer Azure Racetest", "company": "Epam", "source": "LinkedIn",
                                        "url": "https://example.invalid/job-b", "recruiter_email": "", "description": "Azure lead role."})
ja, jb = (ja["id"] if isinstance(ja, dict) else ja), (jb["id"] if isinstance(jb, dict) else jb)

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
shot = os.path.join(tmp_dir, "race.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.83.0" in page.content(), "cache-buster not bumped to 5.83.0")
        page.wait_for_function("typeof openOptimizePanel === 'function'", timeout=15000)
        # job A starts optimizing (slow)...
        page.evaluate(f"openOptimizePanel({{ mode: 'job', candId: {cid}, jobId: {ja}, title: 'A', jdText: {json.dumps(JD_A)} }})")
        page.wait_for_function("document.getElementById('do-status').innerText.includes('Optimizing')", timeout=10000)
        # ...the window is closed and job B (JD not readable) is opened
        page.click("#btn-close-draft-optimize")
        page.evaluate(f"openOptimizePanel({{ mode: 'job', candId: {cid}, jobId: {jb}, title: 'B', jdText: '', summary: 'Lead Engineer Azure Racetest - Epam' }})")
        page.evaluate("document.getElementById('do-status').innerHTML = 'B: paste the job description'")
        page.wait_for_timeout(8000)          # A's answer arrives meanwhile
        check(page.inner_text("#do-status") == "B: paste the job description", f"B's status untouched: {page.inner_text('#do-status')!r}")
        check(page.input_value("#do-optimized") == "" and not page.is_visible("#do-cloud"), "A's resume / cloud check not shown in B")
        check(page.is_disabled("#btn-do-save"), "nothing can be saved under B")
        page.screenshot(path=shot)
        check(resume_versions.list_for(cid) == [], "no version saved")
        # A's result was kept for A: reopening A shows it with no new AI run
        ai_calls.clear()
        page.click("#btn-close-draft-optimize")
        page.evaluate(f"openOptimizePanel({{ mode: 'job', candId: {cid}, jobId: {ja}, title: 'A', jdText: {json.dumps(JD_A)} }})")
        page.wait_for_function("document.getElementById('do-status').innerText.includes('earlier result')", timeout=10000)
        check("on AWS" in page.input_value("#do-optimized") and "rewrite" not in ai_calls and "analysis" not in ai_calls,
              f"A's late result kept for A, no new optimizer run: {ai_calls}")
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
print(f"PASS: optimize race - a late answer for job A never fills job B's window or gets saved under B; it is kept for A. Screenshot: {shot}")
