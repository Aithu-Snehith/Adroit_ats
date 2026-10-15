"""📌 Rules per consultant: standing instructions for every email / resume.

1. API: add / list / delete, duplicates and empty refused, private to the consultant's recruiters.
2. Every writer gets them: the first email (application_brief.write), the email chat (outreach_ai),
   the resume chat (/api/resume-editor/edit) and the Resume Optimizer (custom instructions) - and a fact
   written in a rule counts as the recruiter's own words for the guards.
3. The chat offers '📌 Save as a rule' when the recruiter says "every email / always" (save_rule).
4. Edge (Resume Optimizer tab): the chat's Save-as-rule button, the '📌 Rules (n)' link, the Rules window
   (add, list, delete).
The AI is a stub. Labelled test fixtures. Run: python test_consultant_rules.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_rules.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import application_brief  # noqa: E402
import consultant_rules  # noqa: E402
import docx  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import outreach_ai  # noqa: E402
import resume_bot as rb  # noqa: E402
import resume_editor  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
failures, prompts, optimize_calls = [], {"write": [], "email": [], "resume": []}, []


def check(cond, msg):
    if not cond:
        failures.append(msg)


EMAIL_RULE = "Start every email with 'Hi {first name},' and say we came across the posting on Dice, Indeed or Monster."
RESUME_RULE = "Keep the headline as Java Developer."
RATE_RULE = "His rate is $80/hr on C2C."


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    if system == application_brief.WRITE_SYSTEM:
        prompts["write"].append(prompt)
        return json.dumps({"body": "Hi Asha,\n\nI came across the posting on Dice.\n\nBest regards,\nRuleo Testcase", "needs_input": []}), False, "stub"
    if system == outreach_ai.SYSTEM:
        prompts["email"].append(prompt)
        cur_subject = prompt.split("CURRENT SUBJECT:\n", 1)[1].split("\n\nCURRENT EMAIL:", 1)[0]
        cur_body = prompt.split("CURRENT EMAIL:\n", 1)[1].split("\n\nRECRUITER'S MESSAGE:", 1)[0]
        return json.dumps({"subject": cur_subject, "body": cur_body + "\nMy rate is $80/hr.", "reply": "Added the rate.",
                           "save_rule": "Always mention the rate", "suggestions": []}), False, "stub"
    if system == resume_editor.SYSTEM:
        prompts["resume"].append(prompt)
        return json.dumps({"edits": [], "reply": "I'll keep the headline as Java Developer in every resume.",
                           "save_rule": "Keep the headline as Java Developer in every resume.",
                           "suggestions": [{"label": "Shorter summary", "instruction": "Make the summary shorter"}]}), False, "stub"
    return json.dumps({}), False, "stub"


rb._generate = fake_generate
rb.gemini_configured = lambda: False
rb.ai_configured = lambda: True


def fake_optimize(resume_text, jd_text, custom_instructions="", docx_bytes=None):
    optimize_calls.append(custom_instructions)
    return {"ai_powered": True, "initial_match_percentage": 75, "optimized": False, "match_decision": "Strong",
            "updated_resume_text": resume_text, "mandatory_matched_skills": ["Java"]}


rb.optimize_resume_for_jd = fake_optimize

d = docx.Document()
for line in ["RULEO TESTCASE", "Java Developer", "SKILLS", "Java, Spring Boot"]:
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
DOCX = buf.getvalue()

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "rules-rec@example.invalid", "Rules-1"
REC = uid(models.create_user("Rina Rulestest", EMAIL, PW, role="Recruiter"))
OTHER = uid(models.create_user("Other Rulestest", "rules-other@example.invalid", "Rules-2", role="Recruiter"))
cid = models.create_candidate("Ruleo Testcase", "ruleo@example.invalid", title="Java Developer", primary_skills="Java, Spring Boot",
                              experience_years=6, assigned_user_id=REC)
models.update_candidate(cid, resume_text="RULEO TESTCASE\nJava Developer\nSKILLS\nJava, Spring Boot", resume_filename="Ruleo_Resume.docx")
models.save_resume_file(cid, "Ruleo_Resume.docx", DOCX)
job = models.save_or_update_scraped_job({"title": "Java Backend Developer", "company": "Acme Rules", "recruiter_email": "asha@acme-rules.example",
                                         "recruiter_name": "Asha Rules", "description": "Java Backend Developer: Java, Spring Boot, REST.",
                                         "url": "https://example.invalid/rules-job"})
job_id = job["id"] if isinstance(job, dict) else job

client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Rina Rulestest", "role": "Recruiter", "email": EMAIL}

# ---- 1. API
r = client.post(f"/api/consultants/{cid}/rules", json={"kind": "email", "text": EMAIL_RULE})
check(r.status_code == 200 and [x["text"] for x in r.get_json()["rules"]] == [EMAIL_RULE], f"email rule saved: {r.get_json()}")
check(client.post(f"/api/consultants/{cid}/rules", json={"kind": "email", "text": EMAIL_RULE}).status_code == 400, "duplicate refused")
check(client.post(f"/api/consultants/{cid}/rules", json={"kind": "email", "text": "  "}).status_code == 400, "empty refused")
check(client.post(f"/api/consultants/{cid}/rules", json={"kind": "other", "text": "x y z"}).status_code == 400, "only email / resume")
client.post(f"/api/consultants/{cid}/rules", json={"kind": "resume", "text": RESUME_RULE})
rules = client.get(f"/api/consultants/{cid}/rules").get_json()["rules"]
check(sorted(x["kind"] for x in rules) == ["email", "resume"], f"both kinds listed: {rules}")
with client.session_transaction() as s_:
    s_["user"] = {"id": OTHER, "name": "Other", "role": "Recruiter", "email": "rules-other@example.invalid"}
check(client.get(f"/api/consultants/{cid}/rules").status_code == 404, "another recruiter can't see them")
check(client.post(f"/api/consultants/{cid}/rules", json={"kind": "email", "text": "hack"}).status_code == 404, "or add one")
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Rina Rulestest", "role": "Recruiter", "email": EMAIL}

# ---- 2. every writer gets the rules
cand, jobrow = models.get_candidate_by_id(cid), models.get_job_by_id(job_id)
application_brief.write(cand, jobrow, "Java Backend Developer: Java, Spring Boot", "RULEO TESTCASE\nJava Developer", {})
check(EMAIL_RULE in prompts["write"][-1] and "CONSULTANT RULES" in prompts["write"][-1], "the first email follows the email rules")
check(RESUME_RULE not in prompts["write"][-1], "resume rules stay out of emails")
out = outreach_ai.edit_draft(cand, jobrow, "add the rate", "Application", "Hi Asha,\n\nBest regards")
check(EMAIL_RULE in prompts["email"][-1], "the email chat follows the email rules")
check(not out["applied"] and "$80/hr" in out["reply"], f"a rate nobody stated is refused: {out['reply']}")
check(out.get("save_rule") == "Always mention the rate", "the chat offers a rule to save")
client.post(f"/api/consultants/{cid}/rules", json={"kind": "email", "text": RATE_RULE})
out = outreach_ai.edit_draft(cand, jobrow, "add the rate", "Application", "Hi Asha,\n\nBest regards")
check(out["applied"], f"a fact written in a rule counts as the recruiter's own words: {out['reply']}")
import base64  # noqa: E402
r = client.post("/api/resume-editor/edit", json={"candidate_id": cid, "docx_base64": base64.b64encode(DOCX).decode(), "instruction": "always keep the headline"})
check(RESUME_RULE in prompts["resume"][-1] and EMAIL_RULE not in prompts["resume"][-1], "the resume chat follows the resume rules")
check(r.get_json().get("save_rule", "").startswith("Keep the headline"), "the resume chat offers a rule to save")
r = client.post("/api/resume-bot/optimize", json={"candidate_id": cid, "resume_text": "RULEO TESTCASE\nJava Developer", "jd_text": "Java Backend Developer: Java, Spring Boot, REST APIs."})
check(optimize_calls and RESUME_RULE in optimize_calls[-1], f"the Resume Optimizer gets the resume rules: {optimize_calls[-1:]!r}")
check(consultant_rules.prompt_block(999999, "email") == "", "no rules -> nothing added")

# delete via API
rid = [x for x in client.get(f"/api/consultants/{cid}/rules").get_json()["rules"] if x["text"] == RATE_RULE][0]["id"]
check(all(x["text"] != RATE_RULE for x in client.delete(f"/api/consultants/{cid}/rules/{rid}").get_json()["rules"]), "rule deleted")

# ---- 4. the screen
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "rules.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PW)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.87.0" in page.content(), "cache-buster not bumped to 5.87.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)
        page.click("a.nav-item[data-tab=resumebot]")
        page.select_option("#resumebot-consultant-select", str(cid))
        page.wait_for_function("document.getElementById('resumebot-resume-text').value.includes('RULEO')", timeout=10000)
        page.fill("#resumebot-jd-text", "Java Backend Developer: Java, Spring Boot, REST APIs and microservices on AWS.")
        page.click("#btn-run-resume-optimization")
        page.wait_for_function("document.querySelector('#rbw-preview .docx-wrapper')", timeout=20000)
        page.wait_for_function("document.getElementById('rbw-rules').innerText === '📌 Rules (1)'", timeout=10000)
        page.fill("#rbw-instr", "always keep the headline as Java Developer")
        page.click("#rbw-send")
        page.wait_for_selector("#rbw-chat-msgs .chat-save-rule", timeout=15000)
        check("Shorter summary" in page.inner_text("#rbw-chat-msgs") and page.locator("#rbw-chat-msgs .rv-sugg").count() == 1, "suggestion with Apply shown")
        page.click("#rbw-chat-msgs .chat-save-rule")
        page.wait_for_function("document.getElementById('rbw-rules').innerText === '📌 Rules (2)'", timeout=10000)
        page.click("#rbw-rules")
        page.wait_for_function("document.getElementById('modal-rules').style.display === 'flex'", timeout=5000)
        check("Rules for Ruleo Testcase" in page.inner_text("#rules-title"), "window names the consultant")
        txt = page.inner_text("#modal-rules")
        check(EMAIL_RULE in txt and RESUME_RULE in txt and "Keep the headline as Java Developer in every resume." in txt, f"rules listed: {txt[:300]!r}")
        page.select_option("#rules-kind", "email")
        page.fill("#rules-text", "Mention that the consultant is available immediately.")
        page.click("#rules-add")
        page.wait_for_function("document.getElementById('rules-email').innerText.includes('available immediately')", timeout=5000)
        n_before = page.locator("#rules-resume .rules-del").count()
        page.locator("#rules-resume .rules-del").first.click()
        page.wait_for_function(f"document.querySelectorAll('#rules-resume .rules-del').length === {n_before - 1}", timeout=5000)
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()

conn = models.get_db_connection()
final = consultant_rules.list_for(conn, cid)
conn.close()
check(sorted((x["kind"], x["text"]) for x in final) == sorted([("email", EMAIL_RULE), ("email", "Mention that the consultant is available immediately."),
                                                              ("resume", "Keep the headline as Java Developer in every resume.")]), f"saved state: {final}")
check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: consultant rules - API + privacy, followed by the first email, both chats and the optimizer, rule facts count as the recruiter's, save-as-rule from the chat, Rules window. Screenshot: {shot}")
