"""Paste Requirement & Draft: AI reading, match %, "already applied" warning, and the shared email section.

- resume_bot.match_check runs only the optimizer's step 1 and caches it: an Optimize right after makes
  no second analysis call.
- /api/outreach/read-requirement: AI fields (an email address not in the text is dropped), the JD's
  application instructions, and this consultant's applications for the same requirement in the last 30
  days; duplicates_only re-checks without a new AI read.
- The email preview and Ask AI work before any job is saved (pasted fields), and paste-and-draft drafts
  exactly the email shown.
- In Edge: pasting fills empty fields (never what was typed), shows "AI read", the match %, the
  duplicate warning and the email section with its checklist; the draft gets my edited email to the JD's
  address; a weak match is flagged.
The AI and Gmail are stubs - no network, no cost. Labelled test fixtures.
Run: python test_paste_draft_ai.py
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
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_paste_ai.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import docx  # noqa: E402
import app as app_module  # noqa: E402
import application_brief as ab  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


RAW = """Hi, we have an urgent requirement.
Role: Senior DevOps Engineer
Client: Acme Bank (Hyderabad)
Skills: AWS, Terraform, Kubernetes, GitHub Actions
How to apply: email jobs@acmebank.example with the subject line Senior DevOps Engineer Application and include your notice period.
Thanks, Priya - priya@talentvendor.example"""
RESUME = "ARUN PASTETEST\nSenior DevOps Engineer, 8 years. Terraform on AWS, EKS clusters, GitHub Actions pipelines at Globex."
ANALYSIS = {"initial_match_percentage": 74, "match_breakdown": {"mandatory_skills": 30, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 7, "certifications_education": 5, "location_work_authorization": 2},
            "domain_detected": "Banking", "strong_match_skills": ["Terraform", "Kubernetes"], "partial_match_skills": [],
            "mandatory_missing_skills": [], "preferred_missing_skills": [], "risky_skills_avoided": [],
            "skills_to_add": {"summary": [], "technical_skills": [], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 80}
BODY = ("Hi Priya,\n\nI am interested in the Senior DevOps Engineer role at Acme Bank.\n\nAt Globex I ran Terraform on AWS, EKS clusters and "
        "GitHub Actions pipelines.\n\nNotice period: [please fill: notice period]\n\nBest regards,\nArun Pastetest")
calls = []


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    calls.append(system[:40] + (" [json]" if want_json else " [text]"))
    if system == app_module._READ_SYSTEM:
        return json.dumps({"title": "Senior DevOps Engineer", "company": "Acme Bank", "location": "Hyderabad", "recruiter_email": "invented@nowhere.example",
                           "rate": "", "employment_type": "Full-time", "skills": ["AWS", "Terraform", "Kubernetes"]}), False, "stub"
    if system == ab.BRIEF_SYSTEM:
        return json.dumps({"apply_to_email": "jobs@acmebank.example", "required_subject": "Senior DevOps Engineer Application",
                           "questions": [], "required_details": ["notice period"], "experience_min": None, "experience_max": None,
                           "must_have_skills": ["AWS", "Terraform"]}), False, "stub"
    if system == ab.WRITE_SYSTEM:
        write_prompts.append(prompt)
        return json.dumps({"body": BODY, "answered": [], "needs_input": []}), False, "stub"
    if want_json:       # optimizer step 1 (analysis)
        return json.dumps(ANALYSIS), False, "stub"
    return REWRITE["text"], False, "stub"


write_prompts = []
REWRITE = {"text": ""}      # "" = the optimizer changes nothing (resume left as it is)
rb._generate = fake_generate
rb.ai_configured = lambda: True
rb.gemini_configured = lambda: False

# ---- match_check shares the optimizer's cache
d = docx.Document()
for line in RESUME.split("\n"):
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
DOCX = buf.getvalue()
calls.clear()
m = rb.match_check("", RAW, docx_bytes=DOCX)
check(m.get("match") == 74 and m["matched"] == ["Terraform", "Kubernetes"], f"match check: {m}")
analysis = lambda: [c for c in calls if c.startswith("MASTER") and c.endswith("[json]")]  # noqa: E731
check(len(analysis()) == 1, f"match check = one analysis call: {calls}")
rb.optimize_resume_for_jd("", RAW, docx_bytes=DOCX)
check(len(analysis()) == 1 and any(c.startswith("MASTER") and c.endswith("[text]") for c in calls),
      f"Optimize after the match check reuses its analysis (only the rewrite runs): {calls}")

# ---- fixtures
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "paste-rec@example.invalid", "Paste-1"
REC = {"id": uid(models.create_user("Ravi Pastetest", EMAIL, PW, role="Recruiter")), "name": "Ravi Pastetest", "role": "Recruiter"}
cid = models.create_candidate("Arun Pastetest", "arun.paste@gmail.example", phone="+1 469 555 0109", title="Senior DevOps Engineer",
                              primary_skills="AWS, Terraform, Kubernetes", experience_years=8, assigned_user_id=REC["id"])
models.update_candidate(cid, gmail_account="arun.paste@gmail.example", gmail_app_password="fake app pass not real", resume_text=RESUME)
models.save_resume_file(cid, "Arun_Resume.docx", DOCX)
old = models.save_or_update_scraped_job({"title": "Senior DevOps Engineer", "company": "Acme Bank", "source": "Manual Paste", "url": "",
                                         "recruiter_email": "someone@else.example", "description": "x"})
models.mark_job_drafted(old["id"] if isinstance(old, dict) else old, cid, "d-0", user_id=REC["id"], recruiter_email="someone@else.example")

client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {**REC, "email": EMAIL}

r = client.post("/api/outreach/read-requirement", json={"raw_text": RAW, "candidate_id": cid}).get_json()
check(r["title"] == "Senior DevOps Engineer" and r["company"] == "Acme Bank" and r["location"] == "Hyderabad" and r["ai"], f"AI fields: {r}")
check(r["recruiter_email"] == "jobs@acmebank.example" or r["recruiter_email"] == "priya@talentvendor.example",
      f"invented address dropped, a real one from the text used: {r['recruiter_email']}")
check(r["brief"]["apply_to_email"] == "jobs@acmebank.example" and r["brief"]["required_subject"] == "Senior DevOps Engineer Application", f"brief: {r['brief']}")
check(len(r["duplicates"]) == 1 and r["duplicates"][0]["why"] == "same requirement", f"already applied: {r['duplicates']}")
n = len(calls)
r2 = client.post("/api/outreach/read-requirement", json={"duplicates_only": True, "candidate_id": cid, "title": "Other Role", "company": "",
                                                        "recruiter_email": "someone@else.example"}).get_json()
check(len(calls) == n and r2["duplicates"] and r2["duplicates"][0]["why"] == "same recruiter", f"duplicates only, no AI: {r2}")
check(client.post("/api/outreach/read-requirement", json={"raw_text": "short"}).status_code == 400, "too short refused")
mc = client.post("/api/outreach/match-check", json={"candidate_id": cid, "jd_text": RAW}).get_json()
check(mc.get("match") == 74, f"match-check route: {mc}")
pv = client.post("/api/outreach/application-preview", json={"candidate_id": cid, "job_title": "Senior DevOps Engineer", "company": "Acme Bank",
                                                           "jd_text": RAW, "to_email": "priya@talentvendor.example"}).get_json()
check(pv["to"] == "jobs@acmebank.example" and pv["subject"] == "Senior DevOps Engineer Application" and "[please fill: notice period]" in pv["body"],
      f"preview before any job exists: {pv.get('to')} / {pv.get('subject')}")
ai = client.post("/api/ai/personalize-draft", json={"candidate_id": cid, "job_title": "Senior DevOps Engineer", "company": "Acme Bank", "jd_text": RAW,
                                                   "instruction": "shorter", "current_subject": pv["subject"], "current_body": pv["body"]})
check(ai.status_code == 200 and ai.get_json().get("success"), f"Ask AI works without a saved job: {ai.status_code}")

# ---- the window (Edge)
captured = []


class FakeIMAP:
    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, dialogs = [], []
shot = os.path.join(tmp_dir, "paste.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda dlg: (dialogs.append(dlg.message), dlg.accept()))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.77.0" in page.content(), "cache-buster not bumped to 5.77.0")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length", timeout=15000)
        page.evaluate(f"openPasteDraftModal({cid})")
        page.fill("#pd-title", "My Own Title")
        page.fill("#pd-raw-text", RAW)
        page.dispatch_event("#pd-raw-text", "input")
        page.wait_for_function("document.getElementById('pd-ai-info').innerText.includes('AI read')", timeout=15000)
        check(page.input_value("#pd-title") == "My Own Title" and page.input_value("#pd-company") == "Acme Bank", "fills empty fields only")
        page.wait_for_function("document.getElementById('pd-ai-info').innerText.includes('Match for this consultant: 74%')", timeout=15000)
        check("Already in the tracker" in page.inner_text("#pd-ai-info"), "duplicate warning shown")
        page.wait_for_function("document.getElementById('pd-email-body') && document.getElementById('pd-email-body').value.includes('Globex')", timeout=15000)
        check(page.input_value("#pd-email-to") == "jobs@acmebank.example" and "notice period" in page.inner_text("#pd-email-checklist"),
              "email section: JD's address + checklist")
        page.fill("#pd-email-body", page.input_value("#pd-email-body") + "\nEDITED IN PASTE WINDOW")
        if not page.input_value("#pd-email"):
            page.fill("#pd-email", "priya@talentvendor.example")
        page.screenshot(path=shot, full_page=True)
        page.click("#btn-submit-paste-draft")
        page.wait_for_function("document.querySelector('.toast-container').innerText.includes('Draft created')", timeout=15000)
        m = captured[-1] if captured else None
        body = m.get_body(preferencelist=("plain",)).get_content() if m else ""
        check(dialogs and "[please fill" in dialogs[0], f"asked before drafting with [please fill]: {dialogs}")
        check(m is not None and m["To"] == "jobs@acmebank.example" and m["Subject"] == "Senior DevOps Engineer Application" and "EDITED IN PASTE WINDOW" in body,
              f"draft = the email shown: {m and (m['To'], m['Subject'])}")

        # ---- Optimize from the Paste window: the email is shown there too and comes back
        page.evaluate(f"openPasteDraftModal({cid})")
        page.fill("#pd-raw-text", RAW + "\nSecond paste.")
        page.dispatch_event("#pd-raw-text", "input")
        page.wait_for_function("document.getElementById('pd-email-body') && document.getElementById('pd-email-body').value.includes('Globex')", timeout=15000)
        page.click("#btn-pd-optimize")
        # (a) resume left unchanged -> the email still appears, written from the ORIGINAL resume
        page.wait_for_function("document.getElementById('do-email-body') && document.getElementById('do-email-body').value.includes('Globex')", timeout=20000)
        check(page.is_visible("#do-email-box") and "was tailored" not in write_prompts[-1], "unchanged resume: email shown, not called 'tailored'")
        page.fill("#do-email-body", page.input_value("#do-email-body") + "\nEDIT IN OPTIMIZE (UNCHANGED)")
        page.click("#btn-close-draft-optimize")
        check(page.input_value("#pd-email-body").endswith("EDIT IN OPTIMIZE (UNCHANGED)"), "closing carries the email back to the Paste window")
        # (b) a real optimization -> Save & use in this draft carries the edited email + the tailored resume
        REWRITE["text"] = "[1] Senior DevOps Engineer, 8 years. Terraform on AWS, EKS clusters for banking, GitHub Actions pipelines at Globex."
        page.click("#btn-pd-optimize")
        page.wait_for_selector("#modal-draft-optimize", state="visible")
        page.click("#btn-do-reoptimize")
        page.wait_for_function("!document.getElementById('btn-do-save').disabled", timeout=20000)
        page.wait_for_function("document.getElementById('do-email-body').value.includes('Globex') && !document.getElementById('btn-do-email-rewrite').disabled", timeout=20000)
        check("was tailored" in write_prompts[-1], "optimized resume: email written as tailored")
        page.fill("#do-email-body", page.input_value("#do-email-body") + "\nEDIT IN OPTIMIZE (SAVED)")
        page.click("#btn-do-save")
        page.wait_for_function("document.getElementById('modal-draft-optimize').style.display === 'none'", timeout=15000)
        check(page.input_value("#pd-email-body").endswith("EDIT IN OPTIMIZE (SAVED)") and "Arun" in page.inner_text("#pd-resume-version"),
              f"Save & use: the email and the tailored resume go to the Paste window: {page.inner_text('#pd-resume-version')!r}")
        n0 = len(captured)
        page.click("#btn-submit-paste-draft")
        page.wait_for_function(f"document.querySelectorAll('.toast').length && {n0} < 99", timeout=5000)
        page.wait_for_timeout(2500)
        m2 = captured[-1] if len(captured) > n0 else None
        b2 = m2.get_body(preferencelist=("plain",)).get_content() if m2 else ""
        att2 = [x.get_filename() for x in m2.iter_attachments()] if m2 else []
        check("EDIT IN OPTIMIZE (SAVED)" in b2 and att2 and att2[0].startswith("Arun"), f"draft = that email + the tailored resume: {att2}")
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
print(f"PASS: Paste & Draft - match check reuses the optimizer cache, AI reading (no invented address), brief, duplicates, preview + Ask AI before a job exists, Edge flow drafts exactly the email shown. Screenshot: {shot}")
