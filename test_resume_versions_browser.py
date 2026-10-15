"""Browser test (Playwright + Microsoft Edge): tailored resume versions on screen.

Paste Requirement & Draft: "Optimize Resume for this JD" opens original vs optimized side by side;
the optimized side is editable; Discard saves nothing; Re-optimize runs again; Save stores
Name_Skill.docx (with the edit) and the draft attaches it ("use original instead" undoes that).
Resume Optimizer tab: the preview becomes editable, "Save version" stores Name_Skill_v2.docx.
Gemini and Gmail are never contacted (stubs). Labelled test fixtures.
Run: python test_resume_versions_browser.py
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
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_versions_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.GEMINI_API_KEY = "test-key-not-real"
import app as app_module  # noqa: E402
import docx  # noqa: E402
import docx_editor  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import resume_versions as rv  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
captured, ai_calls = [], []


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP
ANALYSIS = {"initial_match_percentage": 72, "match_breakdown": {"mandatory_skills": 29, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Retail",
            "strong_match_skills": ["Java Full Stack", "Spring Boot"], "partial_match_skills": [], "mandatory_missing_skills": ["React"],
            "preferred_missing_skills": [], "risky_skills_avoided": [], "skills_to_add": {"summary": [], "technical_skills": ["React"], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 84}


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    # label the optimizer's own calls; email reading / writing calls (Paste window) are "other"
    ai_calls.append("rewrite" if not want_json else "analysis" if rb._ANALYSIS_CONTRACT in system else "other")
    return (json.dumps(ANALYSIS), False, "stub") if want_json else ("[4] Java, Spring Boot, React", False, "stub")


rb._generate = fake_generate
rb.gemini_configured = lambda: True

d = docx.Document()
for line in ["KOUSHIK VERSIONUI", "PROFESSIONAL SUMMARY", "Java developer with 6 years of experience.", "TECHNICAL SKILLS", "Java, Spring Boot"]:
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
EMAIL, PASSWORD = "verui@example.invalid", "VerUi-Test-1"
admin = models.create_user("Version UI Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
cid = models.create_candidate("Koushik Versionui", "koushik-verui@example.invalid", title="Java Developer", primary_skills="Java, Spring Boot",
                              gmail_account="koushik-verui@example.invalid", assigned_user_id=admin_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real", country="United States")
models.save_resume_file(cid, "Koushik_original.docx", buf.getvalue())
JD = "Java Full Stack Developer\nRequired: Java Full Stack, Spring Boot and React.\nSend resumes to rec@example.invalid"

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "versions.png")


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
        check("app.js?v=5.84.0" in page.content(), "cache-buster not bumped to 5.84.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)

        # ---- Paste & Draft: optimize -> discard -> re-optimize -> edit -> save -> draft attaches it
        page.click("#btn-open-paste-draft-modal")
        page.select_option("#pd-consultant-select", str(cid))
        page.fill("#pd-raw-text", JD)
        check("original resume" in page.inner_text("#pd-resume-version"), "starts with the original resume")
        page.click("#btn-pd-optimize")
        page.wait_for_function("document.getElementById('do-optimized').value.includes('React')", timeout=20000)
        check("Java developer with 6 years" in page.input_value("#do-original"), "original shown side by side")
        check(not page.get_attribute("#do-optimized", "readonly"), "optimized side is editable")
        check("Koushik_Versionui_Java_Full_Stack_Developer_Spring_Boot.docx" in page.inner_text("#do-filename"), f"name shown: {page.inner_text('#do-filename')}")
        page.click("#btn-do-discard")
        check(not page.is_visible("#modal-draft-optimize") and rv.list_for(cid) == [], "Discard saves nothing")

        ai_calls.clear()
        page.click("#btn-pd-optimize")
        page.wait_for_function("!document.getElementById('btn-do-save').disabled", timeout=20000)
        page.click("#btn-do-reoptimize")
        page.wait_for_function("!document.getElementById('btn-do-save').disabled", timeout=20000)
        check([c for c in ai_calls if c != "other"] == ["rewrite", "rewrite"], f"same resume + JD: the analysis from the first run is reused: {ai_calls}")
        page.click("#rv-tab-text")
        page.fill("#do-optimized", page.input_value("#do-optimized").replace("Java developer with 6 years", "Java full stack developer with 6 years"))
        page.screenshot(path=shot)
        page.click("#btn-do-save")
        page.wait_for_function("document.getElementById('pd-resume-version').innerText.includes('Koushik_Versionui_Java_Full_Stack_Developer_Spring_Boot.docx')", timeout=10000)
        saved = rv.list_for(cid)
        check(len(saved) == 1 and "Java full stack developer" in docx_editor.extract_text(rv.get(saved[0]["id"])["data"]), "saved with the recruiter's edit")
        page.fill("#pd-email", "rec@example.invalid")
        page.fill("#pd-title", "Java Full Stack Developer")
        page.click("#btn-submit-paste-draft")
        page.wait_for_function("document.getElementById('modal-paste-draft').style.display === 'none'", timeout=15000)
        check([x.get_filename() for x in captured[-1].iter_attachments()] == ["Koushik_Versionui_Java_Full_Stack_Developer_Spring_Boot.docx"], "draft attached the saved version")

        # reopening the modal goes back to the original; "use original instead"
        page.click("#btn-open-paste-draft-modal")
        check("original resume" in page.inner_text("#pd-resume-version"), "reset when the modal reopens")
        page.click("#btn-cancel-paste-draft")

        # ---- Resume Optimizer tab: editable preview + Save version (-> _v2)
        page.click("a.nav-item[data-tab=resumebot]")
        page.select_option("#resumebot-consultant-select", str(cid))
        page.wait_for_function("document.getElementById('resumebot-resume-text').value.includes('KOUSHIK')", timeout=15000)
        page.fill("#resumebot-jd-text", JD)
        page.click("#btn-run-resume-optimization")
        page.wait_for_selector("#btn-save-optimized-version", state="visible", timeout=30000)
        check(not page.get_attribute("#result-preview-text", "readonly"), "preview editable after optimizing")
        check("Koushik_Versionui_Java_Full_Stack_Developer_Spring_Boot_v2.docx" in page.inner_text("#save-version-status"), f"next name is _v2: {page.inner_text('#save-version-status')}")
        page.click("#btn-save-optimized-version")
        page.wait_for_function("document.getElementById('save-version-status').innerText.includes('Saved as')", timeout=10000)
        check(len(rv.list_for(cid)) == 2, "second version saved")
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
print(f"PASS: resume versions in Edge - side-by-side editable optimize in Paste & Draft, Discard/Re-optimize/Save, draft attaches the saved file, Optimizer Save version. Screenshot: {shot}")
