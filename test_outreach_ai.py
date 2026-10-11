"""Outreach email AI chat (outreach_ai.py + /api/ai/personalize-draft + the chat window).

The AI edits the CURRENT draft; the code refuses any edit that adds a rate / years / work
authorization / email / phone that isn't on file or in the recruiter's instruction (a rate in the JOB
DESCRIPTION is the client's budget, never the consultant's); "no change" and "AI unavailable" are
reported honestly with the draft untouched. Opening the window shows what will be attached and BCC'd;
the "Add rate" button only shows when a rate is on file; Undo restores the previous version; the
Save button stays on screen on a laptop-size window. The AI is a stub - no network, no cost.
Labelled test fixtures.
Run: python test_outreach_ai.py
"""
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_outreach_ai.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
import outreach_ai  # noqa: E402
import resume_bot  # noqa: E402
import vendors  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "copilot-rec@example.invalid", "Copilot-1"
REC = {"id": uid(models.create_user("Sana Copilottest", EMAIL, PW, role="Recruiter")), "name": "Sana Copilottest", "role": "Recruiter"}
us_id = models.create_candidate("Arun Copilottest", "arun-cp@example.invalid", phone="+1 469 555 0101", title="DevOps Engineer",
                                primary_skills="AWS, Terraform, Kubernetes", experience_years=8, target_rate="$70/hr",
                                visa_status="H1B", location="Dallas, TX", assigned_user_id=REC["id"])
norate_id = models.create_candidate("Bina Copilottest", "bina-cp@example.invalid", title="Java Developer", primary_skills="Java, Spring",
                                    experience_years=5, location="Austin, TX", assigned_user_id=REC["id"])
j = models.save_or_update_scraped_job({"title": "Senior DevOps Engineer", "company": "Acme Health", "source": "Dice",
                                       "recruiter_email": "ta@acmehealth.example", "url": "https://www.dice.com/job-detail/cp-1",
                                       "description": "Senior DevOps: AWS, Terraform. Budget $65/hr. 10+ years preferred."})
job_id = j["id"] if isinstance(j, dict) else j
cand, job = models.get_candidate_by_id(us_id), models.get_job_by_id(job_id)
SUBJ = "Application: Senior DevOps Engineer - Arun Copilottest"
BODY = "Hi Hiring Team,\n\nI am applying for the Senior DevOps Engineer role.\nI bring 8 years in AWS and Terraform.\n\nBest regards,\nArun Copilottest\n+1 469 555 0101 | arun-cp@example.invalid"

resume_bot.ai_configured = lambda: True
resume_bot.gemini_configured = lambda: False
answer = {}
prompts = []


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    prompts.append((system, prompt))
    if answer.get("raise"):
        raise RuntimeError("Error code: 529 - overloaded")
    return (answer["raw"] if "raw" in answer else json.dumps(answer)), False, "claude-haiku-5-5"


resume_bot._generate = fake_generate


def edit(new_body, instruction="make it short", **extra):
    answer.clear()
    answer.update({"subject": SUBJ, "body": new_body, "changes": ["changed it"], "reply": "Done.", **extra})
    return outreach_ai.edit_draft(cand, job, instruction, SUBJ, BODY)


short = "Hi Hiring Team,\n\nApplying for Senior DevOps - 8 years in AWS and Terraform.\n\nBest regards,\nArun Copilottest\n+1 469 555 0101 | arun-cp@example.invalid"
r = edit(short)
check(r["applied"] and r["body"] == short and r["lines_changed"] >= 1 and r["changes"] == ["changed it"], f"plain edit applied: {r}")
system, prompt = prompts[-1]
check("CURRENT EMAIL" in prompt and BODY in prompt and '"target_rate": "$70/hr"' in prompt and "Never" in system, "AI gets the current draft + facts + rules")
check(edit(BODY + "\nRate: $70/hr.")["applied"], "rate on file allowed")
check(not edit(BODY + "\nRate: $95/hr.")["applied"], "invented rate refused")
r = edit(BODY + "\nRate: $65/hr.")
check(not r["applied"] and "$65/hr" in r["reply"] and r["body"] == BODY, f"the JD's budget is not the consultant's rate: {r['reply']}")
check(edit(BODY + "\nRate: $80/hr.", instruction="say his rate is $80/hr")["applied"], "rate stated by the recruiter allowed")
check(not edit(BODY.replace("8 years", "12 years"))["applied"], "wrong years refused")
check(not edit(BODY + "\nGreen Card holder.")["applied"], "work authorization not on file refused")
check(edit(BODY + "\nH1B, open to C2C.", instruction="mention his H1B and that he's open to C2C")["applied"], "visa on file + recruiter's own words allowed")
check(not edit(BODY + "\nReach me at arun.private@example.invalid")["applied"], "new email address refused")
r = edit(BODY, needs_info=["What is his notice period?"])
check(not r["applied"] and "No change" in r["reply"] and r["needs_info"] == ["What is his notice period?"], f"no change reported honestly: {r}")
answer.clear()
answer["raw"] = "not json at all"
r = outreach_ai.edit_draft(cand, job, "shorter", SUBJ, BODY)
check(not r["applied"] and "couldn't be read" in r["reply"], "unreadable answer -> unchanged")
answer.clear()
answer["raise"] = True
r = outreach_ai.edit_draft(cand, job, "shorter", SUBJ, BODY)
check(not r["applied"] and "couldn't answer" in r["reply"] and r["body"] == BODY, f"AI failure -> unchanged with reason: {r['reply']}")
resume_bot.ai_configured = lambda: False
r = outreach_ai.edit_draft(cand, job, "shorter", SUBJ, BODY)
check(not r["applied"] and "isn't set up" in r["reply"], "no AI key -> says so")
resume_bot.ai_configured = lambda: True

# ---- API: opening the window (attachment + BCC + rate chip), then an instruction
conn = models.get_db_connection()
vendors.import_rows(conn, REC["id"], [{"company": "Acme Health", "email": "lead@acmehealth.example"}])
conn.close()
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {**REC, "email": EMAIL}
d = client.post("/api/ai/personalize-draft", json={"candidate_id": us_id, "job_id": job_id}).get_json()
check(d["success"] and d["body"] and d["subject"] and d["rate_on_file"] is True, f"baseline: {d}")
check(d["bcc"] == ["lead@acmehealth.example"], f"BCC preview: {d['bcc']}")
check(d["attachment"] == "", f"no resume on file -> empty attachment: {d['attachment']!r}")
models.save_resume_file(us_id, "Arun_Resume.docx", b"PK fake docx")
d = client.post("/api/ai/personalize-draft", json={"candidate_id": us_id, "job_id": job_id}).get_json()
check(d["attachment"] == "Arun_Resume.docx" and d["attachment_kind"] == "original resume", f"attachment preview: {d['attachment']} {d['attachment_kind']}")
check(client.post("/api/ai/personalize-draft", json={"candidate_id": norate_id, "job_id": job_id}).get_json()["rate_on_file"] is False, "no rate -> no rate chip")
answer.clear()
answer.update({"subject": SUBJ, "body": short, "changes": ["shortened"], "reply": "Shortened it."})
d = client.post("/api/ai/personalize-draft", json={"candidate_id": us_id, "job_id": job_id, "instruction": "shorter",
                                                   "current_subject": SUBJ, "current_body": BODY}).get_json()
check(d["applied"] and d["body"] == short and d["reply"] == "Shortened it.", f"instruction via API: {d}")

# ---- the window in Edge (laptop size)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
shot = os.path.join(tmp_dir, "copilot.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 700})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.70.0" in page.content(), "cache-buster not bumped to 5.70.0")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length >= 2", timeout=15000)
        page.evaluate(f"openAiCopilotModal({us_id}, {job_id})")
        page.wait_for_function("document.getElementById('copilot-body').value.length > 20", timeout=10000)
        box = page.locator("#btn-copilot-save-draft").bounding_box()
        check(box and box["y"] + box["height"] <= 700, f"Save button on screen at 1366x700: {box}")
        check("Arun_Resume.docx (original resume)" in page.inner_text("#copilot-resume-name"), "attachment shown")
        check("lead@acmehealth.example" in page.inner_text("#copilot-bcc-info"), "BCC shown")
        check(page.is_visible("#copilot-chip-rate") and "Job Hunt AI" in page.inner_text("#modal-ai-copilot"), "rate chip (rate on file) + renamed badge")
        check(page.locator("#copilot-chips", has_text="$95").count() == 0, "no invented-rate quick button")
        before = page.input_value("#copilot-body")
        answer.clear()
        answer.update({"subject": SUBJ, "body": short, "changes": ["shortened to two lines"], "reply": "Shortened it."})
        page.fill("#copilot-user-input", "shorter")
        page.click("#btn-copilot-send")
        page.wait_for_function(f"document.getElementById('copilot-body').value === {json.dumps(short)}", timeout=10000)
        check("line" in page.inner_text("#copilot-changed-note") and "shortened to two lines" in page.inner_text("#copilot-chat-messages"), "change listed")
        page.click("#btn-copilot-undo")
        check(page.input_value("#copilot-body") == before and page.is_disabled("#btn-copilot-undo"), "Undo restores the previous version")
        answer.clear()
        answer.update({"subject": SUBJ, "body": before + "\nRate: $99/hr.", "changes": ["rate"], "reply": "Added rate."})
        page.fill("#copilot-user-input", "add a rate")
        page.click("#btn-copilot-send")
        page.wait_for_function("document.getElementById('copilot-chat-messages').innerText.includes(\"didn't apply\")", timeout=10000)
        check(page.input_value("#copilot-body") == before, "refused edit leaves the draft as it was")
        page.screenshot(path=shot)
        page.evaluate("closeAiCopilotModal()")
        page.evaluate(f"openAiCopilotModal({norate_id}, {job_id})")
        page.wait_for_function("document.getElementById('copilot-body').value.includes('Bina')", timeout=10000)
        check(not page.is_visible("#copilot-chip-rate"), "no rate on file -> no rate button")
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
print(f"PASS: outreach AI chat - edits current draft, guard (rate/years/visa/email/phone, JD budget), honest no-change / failure, attachment + BCC preview, rate chip, Undo, Save on screen. Screenshot: {shot}")
