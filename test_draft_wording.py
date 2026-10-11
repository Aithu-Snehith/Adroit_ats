"""Gmail draft wording uses only facts on file and suits the consultant's market.

Reported (India-based consultant, pasted requirement): subject "Job Application: AI Vibe Coder -
Praveen (4 Yrs Exp | Not Seeking US Relocation (India-Based Roles Only))"; body said "posted on
Manual Paste", "as a AI Automation Engineer on Not Seeking US Relocation (India-Based Roles Only)
visa", pasted the raw requirement ("...Hiring: AI Vibe Coder @Remote Hi, I am looking for ... and
se...."), and offered "C2C terms". Also: no visa on file became "H1B"; the AI Copilot invented a
"$90/hr C2C" rate and "authorized to work in the US" for everyone.

Gmail is never contacted (imaplib stubbed). People are labelled test fixtures.
Run: python test_draft_wording.py
"""
import email
import email.policy
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_wording.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import models  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


captured = []


class FakeIMAP:
    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(raw)

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP

admin = models.create_user("Wording Admin", "wording-admin@example.invalid", "Wording-1", role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
india_id = models.create_candidate(
    "Praveen Wordtest", "praveen-word@example.invalid", phone="7075828135", title="AI Automation Engineer",
    primary_skills="Python, AI Automations, n8n, LangChain, Make.com", experience_years=4,
    visa_status="Not Seeking US Relocation (India-Based Roles Only)", location="Hyderabad, India", country="India",
    gmail_account="praveen-word@example.invalid", assigned_user_id=admin_id)
models.update_candidate(india_id, gmail_app_password="test-app-password-not-real")

RAW_JD = ("Hiring: AI Vibe Coder @Remote Hi, I am looking for an AI Vibe Coder @Remote. Please find the job details below "
          "and send your resume. Skills: Python, LangChain, prompt engineering, n8n workflows. 3-5 years. Immediate joiners.")

client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": admin_id, "name": "Wording Admin", "role": "Admin", "email": "a@example.invalid"}
r = client.post("/api/outreach/paste-and-draft", json={"candidate_id": india_id, "raw_jd_text": RAW_JD,
                                                        "job_title": "AI Vibe Coder", "recruiter_email": "clara@example.invalid"})
check(r.status_code == 200 and (r.get_json() or {}).get("success"), f"paste-and-draft: {r.status_code} {r.get_data(as_text=True)[:200]}")
msg = email.message_from_bytes(captured[-1], policy=email.policy.default) if captured else None
subject = msg["Subject"] if msg else ""
body = ""
if msg:
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            body = part.get_content()
            break

check(subject == "Application: AI Vibe Coder - Praveen Wordtest (4 yrs exp)", f"India subject: {subject!r}")
for bad in ("Manual Paste", "visa", "Relocation", "C2C", "Hiring: AI Vibe Coder @Remote", "and se....", "H1B", "a AI "):
    check(bad not in body, f"India body must not contain {bad!r}:\n{body}")
check("an AI Automation Engineer with 4 years of experience" in body, f"intro: {body}")
check("Python, n8n and LangChain" in body or "Python, LangChain and n8n" in body or "Python, n8n, LangChain" in body,
      f"skills that the requirement mentions: {body}")
check("happy to share any further details" in body and "Phone: 7075828135" in body, f"India closing/signature: {body}")

# ---- US consultant, scraped LinkedIn job: source named, work status included, C2C offered
us = {"name": "Us Wordtest", "title": "Data Engineer", "experience_years": 7, "visa_status": "H1B", "country": "United States",
      "primary_skills": "Python, Spark, Snowflake, Airflow", "phone": "", "email": "us@example.invalid"}
job = {"title": "Senior Data Engineer", "company": "Test Analytics Inc", "source": "LinkedIn (Live 24h)",
       "description": "Posted today on LinkedIn: Senior Data Engineer - Spark, Airflow, AWS."}
b = gm.generate_consultant_pitch(us, job)
check("at Test Analytics Inc on LinkedIn" in b and "a Data Engineer with 7 years of experience (H1B)" in b and "C2C" in b,
      f"US body: {b}")
check("Spark and Airflow" in b and "Phone:" not in b, f"US skills / no empty phone line: {b}")
check(gm.build_subject(us, job) == "Application: Senior Data Engineer - Us Wordtest (7 yrs exp, H1B)", gm.build_subject(us, job))
# no work status on file -> nothing invented
nov = dict(us, visa_status="")
check("H1B" not in gm.generate_consultant_pitch(nov, job) and "H1B" not in gm.build_subject(nov, job), "no invented H1B")
# emphasis note steers order, never pasted
b2 = gm.generate_consultant_pitch(us, job, custom_notes="Emphasize Airflow please")
check("Airflow and Spark" in b2 and "Emphasize" not in b2, f"emphasis note: {b2}")

# ---- AI Copilot for the India consultant: an AI that tries to add US authorization / C2C / an invented
# rate is refused by the code guard - the draft stays as it was (no network: the AI is a stub)
import json  # noqa: E402
import outreach_ai  # noqa: E402
import resume_bot  # noqa: E402

cand = models.get_candidate_by_id(india_id)
jobrow = {"title": "AI Vibe Coder", "company": "Test Client", "source": "Manual Paste", "description": RAW_JD}
base_subject, base_body = gm.build_subject(cand, jobrow), gm.generate_consultant_pitch(cand, jobrow, "")
resume_bot.ai_configured = lambda: True
resume_bot.gemini_configured = lambda: False
for bad in ("I am authorized to work in the US.", "Open to C2C.", "My rate is $90/hr.", "H1B visa holder."):
    resume_bot._generate = lambda c, t, sy, pr, want_json, max_tokens, _b=bad: (json.dumps(
        {"subject": base_subject, "body": base_body + "\n" + _b, "changes": ["added"], "reply": "Done"}), False, "stub")
    res = outreach_ai.edit_draft(cand, jobrow, "make it short", base_subject, base_body)
    check(not res["applied"] and res["body"] == base_body and "didn't apply" in res["reply"],
          f"copilot must refuse {bad!r}: {res['reply']}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: draft wording - India consultant: no visa/C2C/'Manual Paste'/pasted requirement; US: source + work status; matched skills; copilot honest.")
