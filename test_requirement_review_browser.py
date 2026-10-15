"""Requirements from Gmail - Review first (Edge).

A requirement email (a Dice Private Email: plain-text stub, job + signature in HTML) is scanned:
the resume is tailored and the reply written, but NO Gmail draft is made - the row is "Ready to review".
Review opens the Optimize window with the tailored Word resume (changes highlighted) and the prepared
reply (To = the recruiter's own address from the signature) without calling the AI again. The recruiter
edits the email and clicks Create Draft: the draft goes into the vendor's thread with the reviewed resume,
the row becomes "Draft created" and the tracker shows the application. A requirement whose resume needed
no changes is reviewed the same way and the original resume is attached.
Gmail and the AI are stubs. Labelled test fixtures. Run: python test_requirement_review_browser.py
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
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_req_review.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import base64  # noqa: E402
import docx  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import inbox_requirements as iq  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import resume_bot  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
failures, appended, ai_calls = [], [], []
INBOX = {}


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, u, p):
        pass

    def select(self, box, readonly=False):
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        if criteria and criteria[0] == "X-GM-RAW":
            return "OK", [b" ".join(sorted(INBOX, key=int))]
        return "OK", [b""]

    def fetch(self, mid, what):
        return "OK", [(mid + b" (BODY[] {1}", INBOX[mid]), b")"]

    def append(self, folder, flags, when, raw):
        appended.append(email.message_from_bytes(raw, policy=email.policy.default))

    def logout(self):
        pass


iq.imaplib.IMAP4_SSL = FakeIMAP
gm.imaplib.IMAP4_SSL = FakeIMAP
assert not iq.AUTO_DRAFT, "Review first is the default"
resume_bot.ai_configured = lambda: True
resume_bot.gemini_configured = lambda: False


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    ai_calls.append(system[:40])
    if system == iq.EXTRACT_SYSTEM:
        title = "Java Backend Developer" if "java backend" in prompt.lower() else "Platform Engineer"
        return json.dumps({"is_requirement": True, "title": title, "client": "TechDigital Test", "location": "Remote",
                           "employment_type": "W2", "skills": ["Java", "Spring Boot"], "recruiter_email": "",
                           "job_description": f"{title}: Java, Spring Boot, Microservices, REST APIs."}), False, "stub"
    return json.dumps({}), False, "stub"      # brief / email writer -> the standard (fallback) reply


resume_bot._generate = fake_generate


def make_docx(lines):
    d = docx.Document()
    for line in lines:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


ORIGINAL = make_docx(["ABHI REVIEWTEST", "Java Developer", "SKILLS", "Java, Spring Boot"])
TAILORED = make_docx(["ABHI REVIEWTEST", "Java Developer", "SKILLS", "Java, Spring Boot, Microservices, REST APIs"])


def fake_optimize(resume_text, jd_text, custom_instructions="", docx_bytes=None):
    if "platform engineer" in jd_text.lower():       # the resume already fits: no changes
        return {"ai_powered": True, "initial_match_percentage": 88, "optimized": False, "match_decision": "No major changes"}
    return {"ai_powered": True, "initial_match_percentage": 78, "optimized": True, "format_preserved": True,
            "docx_base64": base64.b64encode(TAILORED).decode(), "updated_resume_text": "ABHI REVIEWTEST\nJava Developer\nSKILLS\nJava, Spring Boot, Microservices, REST APIs",
            "mandatory_matched_skills": ["Java", "Spring Boot"], "match_decision": "Strong"}


resume_bot.optimize_resume_for_jd = fake_optimize


def mail(n, frm, subject, plain, html_body, msg_id):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "abhi.reviewtest@gmail.example", subject
    m["Date"] = "Wed, 14 Oct 2026 19:47:00 +0000"
    m["Message-ID"] = msg_id
    m.set_content(plain)
    m.add_alternative(html_body, subtype="html")
    INBOX[str(n).encode()] = m.as_bytes()


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "review-rec@example.invalid", "Review-1"
REC = uid(models.create_user("Rita Reviewtest", EMAIL, PW, role="Recruiter"))
cid = models.create_candidate("Abhi Reviewtest", "abhi.reviewtest@gmail.example", title="Java Developer", primary_skills="Java, Spring Boot",
                              experience_years=6, assigned_user_id=REC)
models.update_candidate(cid, gmail_account="abhi.reviewtest@gmail.example", gmail_app_password="fake app pass not real",
                        resume_text="ABHI REVIEWTEST\nJava Developer\nSKILLS\nJava, Spring Boot")
models.save_resume_file(cid, "Abhi_Resume.docx", ORIGINAL)

mail(1, "Risa Reviewtest <f29-v06-6fz@user.dice.com>", "Java Backend Developer on W2",
     "This is a trusted Dice Private Email. Learn more in the Dice help center.",
     "<p>This is a trusted Dice Private Email.</p><p>Hi, I have a Java Backend Developer role on W2.</p>"
     "<p>Top skills: Java, Spring Boot &amp; Microservices, RESTful APIs</p>"
     "<p>Thanks and Regards,<br>Risa Reviewtest | US IT Recruiter<br>E: risa@techdigital-test.example</p>", "<dice1@user.dice.com>")
mail(2, "Sam Reviewtest <sam@platform-vendor.example>", "Requirement: Platform Engineer",
     "Hi, we are hiring a Platform Engineer - Java, Spring Boot. Please share your resume.",
     "<p>Hi, we are hiring a Platform Engineer - Java, Spring Boot. Please share your resume.</p>", "<plat2@vendor>")

conn = models.get_db_connection()
stats = iq.scan_candidate(conn, models.get_candidate_by_id(cid), REC)
check(stats["review"] == 2 and stats["drafted"] == 0 and not appended, f"scan prepares, creates no draft: {stats}, drafts={len(appended)}")
rows = {r["message_id"]: r for r in iq.list_for(conn, [cid])}
r1, r2 = rows["<dice1@user.dice.com>"], rows["<plat2@vendor>"]
check(r1["status"] == "review" and r1["status_label"] == "Ready to review" and r1["resume_version_id"] and r1["match_pct"] == 78, f"row 1: {r1['status']}")
check(r1["draft_to"] == "risa@techdigital-test.example" and r1["draft_subject"] == "Re: Java Backend Developer on W2" and r1["draft_body"],
      f"reply prepared to the recruiter's own address: {r1['draft_to']} / {r1['draft_subject']}")
check(r2["status"] == "review" and not r2["resume_version_id"] and "unchanged" in r2["reason"], f"row 2 (no changes needed): {r2['reason']}")
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM applications WHERE candidate_id = ?", (cid,))
check(cur.fetchone()[0] == 0, "nothing in the tracker before the review")
conn.close()

client = app_module.app.test_client()
with client.session_transaction() as s_:
    s_["user"] = {"id": REC, "name": "Rita Reviewtest", "role": "Recruiter", "email": EMAIL}
det = client.get(f"/api/requirements/{r1['id']}").get_json()
check(det["draft"]["to"] == "risa@techdigital-test.example" and "Microservices" in det["job"]["description"], f"detail: {det['draft']['to']}")
bad = client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": r2["job_id"], "requirement_id": r1["id"]})
check(bad.status_code == 400, "a requirement can't be used with another job")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors, shot = [], os.path.join(tmp_dir, "req_review.png")
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
        page.evaluate(f"openTracker({cid})")
        page.click("#trk-open-reqs")
        page.wait_for_selector("#rq-body .rq-review", timeout=10000)
        check(page.locator("#rq-body .rq-review").count() == 2 and "Ready to review" in page.inner_text("#rq-body"), "Review offered on both")

        # ---- 1. tailored resume + prepared reply, edited, then the draft
        ai_calls.clear()
        page.click(f"#rq-body .rq-review[data-id='{r1['id']}']")
        page.wait_for_function("document.querySelector('#rv-preview .docx-wrapper') && document.querySelector('#rv-preview').innerText.includes('Microservices')", timeout=15000)
        page.wait_for_function("document.getElementById('do-email-to') && document.getElementById('do-email-to').value", timeout=10000)
        check(not page.is_visible("#modal-reqs"), "the Requirements window steps aside")
        check(page.input_value("#do-email-to") == "risa@techdigital-test.example" and page.input_value("#do-email-subject") == "Re: Java Backend Developer on W2",
              f"prepared reply shown: {page.input_value('#do-email-to')} / {page.input_value('#do-email-subject')}")
        check(page.input_value("#do-email-body") == r1["draft_body"], "the body is exactly the prepared one")
        check(page.locator("#rv-preview .rv-changed").count() >= 1, "tailored lines highlighted")
        check("Create Draft" in page.inner_text("#btn-do-save") and "Review before the draft is created" in page.inner_text("#do-status"), "review wording")
        page.fill("#do-email-body", page.input_value("#do-email-body") + "\nEDITED BY RECRUITER")
        check(ai_calls == [], f"no AI call to open the review: {ai_calls}")
        page.screenshot(path=shot)
        page.click("#btn-do-save")
        page.wait_for_function("!document.getElementById('modal-draft-optimize') || document.getElementById('modal-draft-optimize').style.display === 'none'", timeout=15000)
        page.wait_for_timeout(1500)
        check(len(appended) == 1, f"one draft created: {len(appended)}")
        if appended:
            m = appended[-1]
            check(m["To"] == "risa@techdigital-test.example" and m["In-Reply-To"] == "<dice1@user.dice.com>", f"reply in the vendor's thread: {m['To']} {m['In-Reply-To']}")
            check("EDITED BY RECRUITER" in m.get_body(preferencelist=("plain",)).get_content(), "the reviewed email is what was drafted")
            att = list(m.iter_attachments())
            check(att and att[0].get_payload(decode=True) == TAILORED, f"the reviewed (tailored) resume is attached: {[a.get_filename() for a in att]}")

        # ---- 2. no changes needed: the original resume is shown and attached
        page.evaluate(f"openTracker({cid})")
        page.click("#trk-open-reqs")
        page.wait_for_selector(f"#rq-body .rq-review[data-id='{r2['id']}']", timeout=10000)
        for _ in range(10):        # the draft request may still be finishing (it runs after the window closes)
            if "Draft created after your review" in page.inner_text("#rq-body"):
                break
            page.wait_for_timeout(1000)
            page.evaluate("rqLoad()")
        check(page.locator(f"#rq-body .rq-review[data-id='{r1['id']}']").count() == 0 and "Draft created after your review" in page.inner_text("#rq-body"),
              "the reviewed one is now Draft created")
        page.click(f"#rq-body .rq-review[data-id='{r2['id']}']")
        page.wait_for_function("document.getElementById('do-status').innerText.includes('no changes were needed')", timeout=15000)
        page.wait_for_function("document.getElementById('do-email-to').value === 'sam@platform-vendor.example'", timeout=10000)
        page.click("#btn-do-save")
        page.wait_for_function("document.getElementById('modal-draft-optimize').style.display === 'none'", timeout=15000)
        page.wait_for_timeout(1500)
        check(len(appended) == 2, f"second draft: {len(appended)}")
        if len(appended) == 2:
            att = list(appended[-1].iter_attachments())
            check(att and att[0].get_payload(decode=True) == ORIGINAL and appended[-1]["In-Reply-To"] == "<plat2@vendor>",
                  f"original resume attached, in their thread: {[a.get_filename() for a in att]}")
        browser.close()
finally:
    server.shutdown()

conn = models.get_db_connection()
done = {r["message_id"]: r for r in iq.list_for(conn, [cid])}
check(done["<dice1@user.dice.com>"]["status"] == "drafted" and done["<plat2@vendor>"]["status"] == "drafted", "both rows Draft created")
cur = conn.cursor()
cur.execute("SELECT stage FROM applications WHERE candidate_id = ?", (cid,))
check(sorted(r[0] for r in cur.fetchall()) == ["Drafted", "Drafted"], "both in the tracker as drafted")
conn.close()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: requirement review first - prepared not drafted, Review shows the tailored resume + prepared reply (no AI), edit, draft in the vendor's thread with the reviewed resume; unchanged resume -> original attached. Screenshot: {shot}")
