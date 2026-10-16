"""Application brief (application_brief.py): the draft follows what the JD asks, written from the resume
being attached - tested with the real Creuto DevOps Engineer JD.

The brief keeps only an apply-to address / subject that really appear in the JD; the email answers the
JD's question ("short note about an infrastructure challenge") from the RESUME; a figure that isn't in the
resume is refused and the AI is asked again; if it fails twice, the standard pitch with "[please add]" is
used; missing details become "[please fill: ...]"; the checklist shows To / subject / questions / missing
input / "may look overqualified". Opening the chat with the JD + the saved tailored version: To =
careers@creuto.com, exact subject, written from the tailored resume. An automatic requirement draft whose
JD says to apply elsewhere goes to that address as a new email, flagged "needs your input". The AI and
Gmail are stubs - no network, no cost. Labelled test fixtures.
Run: python test_application_brief.py
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
from email.message import EmailMessage

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_brief.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import base64  # noqa: E402

import docx  # noqa: E402
import app as app_module  # noqa: E402
import application_brief as ab  # noqa: E402
import inbox_requirements as iq  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402
import resume_versions  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


JD = """DevOps Engineer DEPARTMENT Development EMPLOYMENT TYPE Full Time LOCATION India EXPERIENCE 2 + Years
We are looking for a DevOps Engineer with 2 to 5 years of experience. Design, build, and manage cloud infrastructure on AWS or GCP
using Terraform or AWS CDK. Build and maintain CI/CD pipelines using GitHub Actions or CircleCI. Manage containerised workloads
using Docker and Kubernetes. Implement monitoring using Datadog, Grafana, or CloudWatch.
How to Apply: Send your resume and a short note about an infrastructure challenge you solved and the impact it had.
Email us at careers@creuto.com with the subject line DevOps Engineer Application. Apply Now"""
RESUME = ("VISAKH BRIEFTEST\nPROFESSIONAL SUMMARY\nSenior Cloud DevOps Engineer with 10+ years at SunTec Business Solutions.\n"
          "Terraform IaC for multi-region AWS environments; EKS and GKE clusters; Prometheus and Grafana monitoring.\n"
          "PROJECT: Migrated 40 manual deployment jobs to GitHub Actions pipelines for a global banking client.")

BRIEF = {"apply_to_email": "careers@creuto.com", "required_subject": "DevOps Engineer Application",
         "questions": ["a short note about an infrastructure challenge you solved and the impact it had"],
         "required_details": ["notice period"], "experience_min": 2, "experience_max": 5,
         "must_have_skills": ["Terraform", "GitHub Actions", "Kubernetes", "Grafana"]}
GOOD = ("Hi,\n\nI am interested in the DevOps Engineer role at Creuto.\n\nAt SunTec Business Solutions I built Terraform IaC for "
        "multi-region AWS environments and ran EKS and GKE clusters with Prometheus and Grafana monitoring.\n\n"
        "Infrastructure challenge I solved:\nA global banking client relied on 40 manual deployment jobs. I moved them to GitHub Actions "
        "pipelines, which made releases repeatable. [please add: measurable impact]\n\nNotice period: [please fill: notice period]\n\n"
        "My resume is attached.\n\nBest regards,\nVisakh Brieftest")
BAD = GOOD.replace("which made releases repeatable.", "cutting release time by 75%.")
writes = []
script = {"brief": BRIEF, "writes": [GOOD]}


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    if system == ab.BRIEF_SYSTEM:
        return json.dumps(script["brief"]), False, "stub"
    if system == ab.WRITE_SYSTEM:
        writes.append(prompt)
        body = script["writes"].pop(0) if len(script["writes"]) > 1 else script["writes"][0]
        return json.dumps({"body": body, "answered": BRIEF["questions"], "needs_input": []}), False, "stub"
    if system == iq.EXTRACT_SYSTEM:
        return json.dumps({"is_requirement": True, "title": "DevOps Engineer", "client": "Creuto", "recruiter_name": "",
                           "recruiter_email": "", "job_description": JD}), False, "stub"
    return json.dumps({}), False, "stub"


resume_bot._generate = fake_generate
resume_bot.ai_configured = lambda: True
resume_bot.gemini_configured = lambda: False

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "brief-rec@example.invalid", "Brief-1"
REC = {"id": uid(models.create_user("Ravi Brieftest", EMAIL, PW, role="Recruiter", region="India")), "name": "Ravi Brieftest", "role": "Recruiter"}
cid = models.create_candidate("Visakh Brieftest", "visakh.brief@gmail.example", phone="+91 62351 79249", title="Senior Cloud DevOps Engineer",
                              primary_skills="AWS, Terraform, Kubernetes", experience_years=10, assigned_user_id=REC["id"])
models.update_candidate(cid, country="India", resume_text="(original resume text)", gmail_account="visakh.brief@gmail.example",
                        gmail_app_password="fake app pass not real")
cand = models.get_candidate_by_id(cid)
j = models.save_or_update_scraped_job({"title": "DevOps Engineer", "company": "Creuto", "source": "LinkedIn", "url": "https://www.linkedin.com/jobs/view/brief-1",
                                       "recruiter_email": "", "description": JD})
job_id = j["id"] if isinstance(j, dict) else j
job = models.get_job_by_id(job_id)

# ---- brief: only what the JD really says
script["brief"] = dict(BRIEF, apply_to_email="hr@invented.example", required_subject="Not In The JD")
b = ab.brief(JD)
check(b["apply_to_email"] == "" and b["required_subject"] == "", f"invented address / subject dropped: {b}")
script["brief"] = BRIEF
b = ab.brief(JD)
check(b["apply_to_email"] == "careers@creuto.com" and b["required_subject"] == "DevOps Engineer Application" and b["experience_max"] == 5, f"brief: {b}")

# ---- write: from the resume, figures checked, fallback
w = ab.write(cand, job, JD, RESUME, b)
check(not w["fallback"] and "Infrastructure challenge I solved" in w["body"] and w["needs_input"] == ["[please add: measurable impact]", "[please fill: notice period]"],
      f"good draft accepted: {w}")
check("RESUME:" in writes[-1] and "40 manual deployment jobs" in writes[-1] and "careers@creuto.com" in writes[-1], "the AI gets the resume and the brief")
writes.clear()
script["writes"] = [BAD, GOOD]
w = ab.write(cand, job, JD, RESUME, b)
check(len(writes) == 2 and "75%" in writes[1] and not w["fallback"] and "75%" not in w["body"], f"invented figure refused, asked again: {len(writes)}")
script["writes"] = [BAD]
w = ab.write(cand, job, JD, RESUME, b)
check(w["fallback"] and "75%" not in w["body"] and "[please add: a short note about an infrastructure challenge" in w["body"]
      and "[please fill: notice period]" in w["body"], f"twice wrong -> standard pitch + [please add]: {w['body'][-400:]}")
script["writes"] = [GOOD]

# ---- checklist
cl = ab.checklist(b, "careers@creuto.com", "DevOps Engineer Application", GOOD, cand)
texts = " | ".join(("OK " if i["ok"] else "WARN ") + i["text"] for i in cl)
check("OK Send to careers@creuto.com" in texts and "OK Subject exactly" in texts and "WARN Needs your input: please fill: notice period" in texts
      and "overqualified" in texts, f"checklist: {texts}")
check(not ab.checklist(b, "someone@else.example", "Application", GOOD, cand)[0]["ok"], "wrong To flagged")

# ---- opening the chat with the JD + the saved tailored version
d = docx.Document()
for line in RESUME.split("\n"):
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
vid = resume_versions.save(cid, job_id, "Visakh_Brieftest_Terraform.docx", buf.getvalue(), "Terraform", "", JD[:300], REC["id"])
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {**REC, "email": EMAIL}
writes.clear()
r = client.post("/api/ai/personalize-draft", json={"candidate_id": cid, "job_id": job_id, "jd_text": JD, "optimized_resume_id": vid}).get_json()
check(r["to_email"] == "careers@creuto.com" and r["subject"] == "DevOps Engineer Application" and "Infrastructure challenge" in r["body"],
      f"chat opens with the brief-based email: {r.get('to_email')} / {r.get('subject')}")
check(writes and "40 manual deployment jobs" in writes[-1] and "(original resume text)" not in writes[-1], "written from the TAILORED resume")
check(r["attachment"] == "Visakh_Brieftest_Terraform.docx" and any("overqualified" in i["text"] for i in r["checklist"]), f"attachment + checklist: {r['attachment']}")

# ---- preview inside the Optimize window: written from the resume text ON SCREEN (not saved yet)
writes.clear()
r = client.post("/api/outreach/application-preview", json={"candidate_id": cid, "job_id": job_id, "jd_text": JD,
                                                           "resume_text": RESUME + "\nON SCREEN EDIT", "to_email": "row@example.invalid"}).get_json()
check(writes and "ON SCREEN EDIT" in writes[-1] and r["to"] == "careers@creuto.com" and r["subject"] == "DevOps Engineer Application"
      and r["checklist"], f"preview from the on-screen resume: {r.get('to')}")

# ---- automatic requirement draft whose JD says "email careers@..." -> new email there, needs input
appended = []


class FakeIMAP:
    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def select(self, *a, **k):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        return ("OK", [b"1"]) if criteria and criteria[0] == "X-GM-RAW" else ("OK", [b""])

    def fetch(self, mid, what):
        m = EmailMessage()
        m["From"], m["Subject"], m["Message-ID"] = "Vendor <jobs@vendor.example>", "Hiring: DevOps Engineer - Creuto", "<v1@vendor>"
        m["Date"] = "Mon, 12 Oct 2026 07:00:00 +0000"
        m.set_content("We are hiring - see below.\n" + JD)
        return "OK", [(b"1 (BODY[] {1}", m.as_bytes()), b")"]

    def append(self, folder, flags, when, raw):
        appended.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


iq.imaplib.IMAP4_SSL = FakeIMAP
iq.AUTO_DRAFT = True      # this part checks the automatic-draft mode (Review first: test_requirement_review_browser)
models.save_resume_file(cid, "Visakh_Resume.docx", buf.getvalue())
resume_bot.optimize_resume_for_jd = lambda *a, **k: {"ai_powered": True, "initial_match_percentage": 82, "optimized": True,
                                                      "docx_base64": base64.b64encode(buf.getvalue()).decode(), "mandatory_matched_skills": ["Terraform"]}
conn = models.get_db_connection()
st = iq.scan_candidate(conn, models.get_candidate_by_id(cid), REC["id"])
row = iq.list_for(conn, [cid])[0]
conn.close()
m = appended[-1] if appended else None
check(st["drafted"] == 1 and m is not None and m["To"] == "careers@creuto.com" and m["Subject"] == "DevOps Engineer Application" and not m["In-Reply-To"],
      f"applied where the JD says, as a new email: {m and (m['To'], m['Subject'], m['In-Reply-To'])}")
check(row["status"] == "drafted_input" and "notice period" in row["reason"] and "careers@creuto.com" in row["reason"], f"flagged needs input: {row['status']} {row['reason']}")

# ---- the window (Edge): checklist + confirm before saving with [please fill]
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

created = []
import gmail_multi_manager  # noqa: E402
_orig = gmail_multi_manager.create_candidate_draft


def spy(*a, **k):
    created.append(k)
    return {"success": True, "candidate_name": "Visakh Brieftest", "resume_attached": True}


gmail_multi_manager.create_candidate_draft = spy
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, dialogs = [], []
shot = os.path.join(tmp_dir, "brief.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda dlg: (dialogs.append(dlg.message), dlg.accept()))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to 5.88.0")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length", timeout=15000)
        page.evaluate(f"openAiCopilotModal({cid}, {job_id}, {{ jdText: {json.dumps(JD)}, versionId: {vid} }})")
        page.wait_for_function("document.getElementById('copilot-body').value.includes('Infrastructure challenge')", timeout=15000)
        cl_txt = page.inner_text("#copilot-checklist")
        check(page.is_visible("#copilot-checklist") and "careers@creuto.com" in cl_txt and "⚠" in cl_txt and "✓" in cl_txt, f"checklist shown: {cl_txt!r}")
        check(page.input_value("#copilot-to-email") == "careers@creuto.com" and page.input_value("#copilot-subject") == "DevOps Engineer Application", "To / subject as the JD asks")
        page.screenshot(path=shot)
        page.click("#btn-copilot-save-draft")
        page.wait_for_timeout(1500)
        check(dialogs and "[please fill" in dialogs[0], f"asks before saving with [please fill] parts: {dialogs}")
        check(created and created[-1].get("resume_version_id") == vid, f"draft attaches the saved tailored version: {created and created[-1].get('resume_version_id')}")
        browser.close()
finally:
    server.shutdown()
    gmail_multi_manager.create_candidate_draft = _orig
check(not js_errors, f"JavaScript errors: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: application brief (Creuto JD) - real instructions only, answers from the resume, invented figures refused + retried, fallback with [please add], [please fill] for missing details, checklist, chat uses the tailored version, automatic draft to the JD's address, confirm before saving. Screenshot: {shot}")
