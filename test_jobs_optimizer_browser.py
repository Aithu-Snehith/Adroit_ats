"""Browser test (Playwright + Microsoft Edge): Jobs tab -> Optimize Resume IN PLACE -> draft, and the Applied column.

1. "Browse Jobs" on a consultant (Karun) makes Karun the Target Candidate on every job row.
2. Columns: "Resume" right after "Recruiter Email", then an "Applied" dropdown (Not applied /
   Applied in portal / Email sent / Portal + email) saved per job and kept after a reload.
3. "Optimize Resume" no longer jumps to the Resume Optimizer tab: the panel opens on the Jobs tab
   with the row's Target Candidate and the job description:
     - a pasted requirement -> its full text, optimized straight away;
     - a Dice posting with only a summary -> the full description is read from the posting (once,
       then reused) and optimized;
     - a posting that can't be read -> the summary + the reason; nothing is optimized until the
       full JD is pasted.
   After optimizing, Download saves Name_Skill.docx and downloads it; "Save & Create Draft" creates
   the job's Gmail draft to the row's recruiter email with that same resume attached.

COST SAFETY: scrapers, posting reads, Gemini (resume_bot._generate) and Gmail (IMAP) are stubs;
any request to Apify or a non-local host is aborted. Consultants and jobs are labelled fixtures.
Run: python test_jobs_optimizer_browser.py
"""
import email
import email.policy
import io
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_jobs_opt.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.APIFY_API_TOKEN = ""
config.GEMINI_API_KEY = "test-key-not-real"
import app as app_module  # noqa: E402
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import job_description_fetch  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import resume_versions  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

DICE_FULL = ("FULL DICE DESCRIPTION (test fixture)\nResponsibilities:\n- Build Power BI dashboards\n- Write complex SQL\n"
             "Requirements: 5+ years SQL and Python. " + "Detail. " * 30)
fetch_calls, ai_calls, captured = [], [], []


def _fake_fetch(url):
    fetch_calls.append(url)
    if url and "00000000-0000-4000-8000-000000000001" in url:
        return DICE_FULL, ""
    return None, "The posting is no longer available."


job_description_fetch.fetch_full_description = _fake_fetch
ANALYSIS = {"initial_match_percentage": 75, "match_breakdown": {"mandatory_skills": 30, "recent_project_relevance": 19, "domain_experience": 12,
            "tools_frameworks_cloud": 7, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Finance",
            "strong_match_skills": ["Power BI", "SQL"], "partial_match_skills": [], "mandatory_missing_skills": [], "preferred_missing_skills": [],
            "risky_skills_avoided": [], "skills_to_add": {"summary": ["Power BI"], "technical_skills": [], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 86}


def _fake_generate(client, types, system, prompt, want_json, max_tokens):
    ai_calls.append("analysis" if want_json else "rewrite")
    if want_json:
        return json.dumps(ANALYSIS), False, "stub"
    return "[1] Data Analyst with SQL, Python and Power BI dashboards (test fixture).", False, "stub"


rb._generate = _fake_generate
rb.gemini_configured = lambda: True


class _FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = _FakeIMAP

EMAIL, PASSWORD = "jobs-opt-admin@example.invalid", "JobsOpt-Test-1"
admin = models.create_user("Jobs Opt Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
first_id = models.create_candidate("Aaa First Testcase", "first@example.invalid", title="Java Developer", assigned_user_id=admin_id)
_doc = docx.Document()
_doc.add_paragraph("KARUN TEST RESUME")
_doc.add_paragraph("Data Analyst with SQL, Python and Power BI (test fixture).")
_buf = io.BytesIO()
_doc.save(_buf)
karun_id = models.create_candidate("Karun Testcase", "karun@example.invalid", title="Data Analyst", assigned_user_id=admin_id,
                                   resume_filename="karun_test.docx")
models.save_resume_file(karun_id, "karun_test.docx", _buf.getvalue())
models.update_candidate(karun_id, gmail_account="karun@example.invalid", gmail_app_password="test-app-password-not-real", country="United States")
FULL_JD = ("Data Analyst (Contract) - Test Client. Responsibilities: build Power BI dashboards, write complex SQL, "
           "automate reporting in Python, partner with finance stakeholders, document data lineage, validate data quality, "
           "support month-end close analytics, and present insights to leadership. Requirements: 5+ years SQL, Power BI, "
           "Python (pandas), Azure Synapse a plus, strong communication. This is a pasted test requirement.")


def _job(d):
    j = models.save_or_update_scraped_job(d)
    return j["id"] if isinstance(j, dict) else j


pasted_id = _job({"title": "Data Analyst Pasted Test", "company": "Test Client", "location": "Remote", "job_type": "Contract (C2C)",
                  "salary": "$60/hr", "source": "Manual Paste", "url": "", "recruiter_email": "rec@example.invalid",
                  "description": FULL_JD, "matched_skills": "Data Analyst", "match_score": 95})
scraped_id = _job({"title": "Data Analyst Scraped Test", "company": "Test Corp", "location": "Dallas, TX", "job_type": "Contract",
                   "salary": "$55/hr", "source": "Dice", "url": "https://www.dice.com/job-detail/00000000-0000-4000-8000-000000000001",
                   "description": "Dice US Contract Requisition: Data Analyst Scraped Test at Test Corp (Dallas, TX). Pay Rate: $55/hr",
                   "matched_skills": "Data Analyst", "match_score": 90})
gone_id = _job({"title": "Data Analyst Gone Test", "company": "Gone Corp", "location": "Remote", "source": "LinkedIn (Live 24h)",
                "url": "https://www.linkedin.com/jobs/view/data-analyst-gone-test-9999999999", "description": "Summary only (test).",
                "matched_skills": "Data Analyst", "match_score": 90})

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
failures, js_errors, blocked = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def guard(route):
    url = route.request.url
    if url.startswith(base):
        route.continue_()            # the app's own server (its AI / Gmail / posting reads are stubs)
    elif "api.apify.com" in url:
        blocked.append(url)
        route.abort()
    else:
        route.continue_()            # fonts/CDN for rendering only


ROW = ".job-row[data-job-id='{}']"
shot = os.path.join(tmp_dir, "jobs_optimize.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950}, accept_downloads=True)
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.route("**/*", guard)
        page.goto(base + "/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.70.0" in page.content(), "cache-buster not bumped to 5.70.0")

        # 1. Browse Jobs on Karun (not the first consultant)
        page.click("a.nav-item[data-tab=candidates]")
        btn = page.locator(f"button[onclick^='browseJobsForCandidate({karun_id},']").first
        btn.wait_for(state="visible", timeout=10000)
        btn.click()
        page.wait_for_selector(ROW.format(pasted_id), timeout=20000)
        page.wait_for_timeout(500)
        selected = page.eval_on_selector_all(".job-consultant-select", "els => els.map(e => e.value)")
        check(selected and all(v == str(karun_id) for v in selected), f"every row's Target Candidate should be Karun: {selected}")

        # 2. columns + Applied dropdown
        headers = [h.strip() for h in page.eval_on_selector_all("#jobs-data-table thead th", "els => els.map(e => e.textContent)")]
        i = next((k for k, h in enumerate(headers) if h.startswith("Recruiter Email")), -1)
        check(i >= 0 and headers[i + 1] == "Resume" and headers[i + 2] == "Applied", f"Resume then Applied after Recruiter Email: {headers}")
        cells = page.locator(f"{ROW.format(pasted_id)} td").count()
        check(cells == len(headers), f"row has {cells} cells for {len(headers)} headers")
        opts = page.eval_on_selector_all(f"{ROW.format(pasted_id)} .job-apply-status option", "els => els.map(o => o.textContent)")
        check(opts == ["Not applied", "Applied in portal", "Email sent", "Portal + email"], f"Applied options: {opts}")
        page.select_option(f"{ROW.format(pasted_id)} .job-apply-status", "both")
        page.wait_for_function("document.querySelector('.toast-container') && document.querySelector('.toast-container').innerText.includes('Marked')", timeout=5000)
        check(models.get_job_by_id(pasted_id).get("apply_status") == "both", "Applied status saved per job")

        # 3a. pasted requirement -> panel on the Jobs tab, full JD, optimized straight away
        page.click(f"{ROW.format(pasted_id)} .btn-optimize-job")
        page.wait_for_selector("#modal-draft-optimize", state="visible", timeout=5000)
        check(page.is_visible("#tab-jobs") and not page.is_visible("#tab-resumebot"), "stays on the Jobs tab")
        page.wait_for_function("document.getElementById('do-optimized').value.includes('Power BI dashboards')", timeout=20000)
        check(page.input_value("#do-jd") == FULL_JD, "the pasted requirement's full description is used")
        check("KARUN TEST RESUME" in page.input_value("#do-original"), "Karun's resume on the left")
        check("Data Analyst Pasted Test" in page.inner_text("#do-title"), "panel names the job")
        check(page.is_visible("#btn-do-download") and "Create Draft" in page.inner_text("#btn-do-save"), "Download + Save & Create Draft offered")
        page.screenshot(path=shot)
        with page.expect_download() as dl:
            page.click("#btn-do-download")
        check(dl.value.suggested_filename == "Karun_Testcase_Power_BI.docx", f"download name: {dl.value.suggested_filename}")
        page.click("#btn-do-save")
        page.wait_for_function("document.querySelector('.toast-container').innerText.includes('Gmail Draft Created')", timeout=15000)
        att = [x.get_filename() for x in captured[-1].iter_attachments()] if captured else []
        check(att == ["Karun_Testcase_Power_BI.docx"] and captured and captured[-1]["To"] == "rec@example.invalid",
              f"draft to the row's recruiter with the tailored resume: {att}")
        check(len(resume_versions.list_for(karun_id)) == 1, "Download + Create Draft reused ONE saved version")

        # 3b. Dice posting -> full description read once, then optimized
        page.click(f"{ROW.format(scraped_id)} .btn-optimize-job")
        page.wait_for_function("document.getElementById('do-jd').value.startsWith('FULL DICE DESCRIPTION')", timeout=10000)
        page.wait_for_function("!document.getElementById('btn-do-save').disabled", timeout=20000)
        page.click("#btn-do-discard")
        page.click(f"{ROW.format(scraped_id)} .btn-optimize-job")
        page.wait_for_function("document.getElementById('do-jd').value.startsWith('FULL DICE DESCRIPTION')", timeout=10000)
        check(len(fetch_calls) == 1, f"the posting is read once, then reused: {fetch_calls}")
        page.wait_for_function("!document.getElementById('btn-do-reoptimize').disabled", timeout=20000)
        page.click("#btn-do-discard")

        # 3c. posting that can't be read -> summary + reason, no optimization
        ai_calls.clear()
        page.click(f"{ROW.format(gone_id)} .btn-optimize-job")
        page.wait_for_function("document.getElementById('do-status').innerText.includes('no longer available')", timeout=10000)
        jd = page.input_value("#do-jd")
        check("Data Analyst Gone Test - Gone Corp" in jd and "Summary only (test)." in jd, f"summary kept on failure: {jd!r}")
        check("Only a short summary" in page.inner_text("#do-status") and not ai_calls, "asks for the full JD, nothing optimized")
        check(not page.is_disabled("#btn-do-reoptimize"), "Re-optimize available once the JD is pasted")
        page.click("#btn-do-discard")

        # the Applied status survives a reload
        page.reload()
        page.click("a.nav-item[data-tab=jobs]")
        page.wait_for_selector(ROW.format(pasted_id), timeout=20000)
        check(page.input_value(f"{ROW.format(pasted_id)} .job-apply-status") == "both", "Applied status kept after reload")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
check(not blocked, f"a paid call was attempted (blocked): {blocked}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: Jobs tab - Browse Jobs target, Applied dropdown saved, Optimize Resume in place (pasted / read once / honest fallback), "
      f"Download + Save & Create Draft with the tailored resume. Screenshot: {shot}")
