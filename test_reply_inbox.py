"""Recruiter replies (reply_inbox.py): read, sort, prepare, answer in the same Gmail thread.

Drafts carry a Message-ID kept on the application; only mail FROM each application's recruiter is
fetched, read-only and with BODY.PEEK (stays unread); quoted earlier mail is stripped; each message is
stored once (a second check or a second worker adds nothing); the AI's sorting moves the status
forward (interview -> Interview, rejection -> Rejected, auto-reply -> nothing); replies are prepared
from facts only (an invented rate is refused); "Save reply to Gmail" appends a "Re:" draft with
In-Reply-To / References (+ resume when asked); mode "off" prepares nothing; "send_safe" sends ONLY
resume-request answers (with the resume) over SMTP; other recruiters can't see the replies; the
automatic background check is off in tests. Gmail, SMTP and the AI are stubs - no network, no cost.
Labelled test fixtures.
Run: python test_reply_inbox.py
"""
import email
import email.policy
import json
import os
import socket
import sys
import tempfile
import threading
from email.message import EmailMessage

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_replies.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
os.environ.pop("REPLY_CHECK_MINUTES", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
import reply_inbox as ri  # noqa: E402
import resume_bot  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


check(app_module._reply_minutes == 0, "automatic Gmail checks are off in tests")
REC_ADDR = "ta@acme-staffing.example"
INBOX = {}          # id -> raw bytes
appended, sent, searches, fetches, selected = [], [], [], [], []


def mail(mid_num, frm, subject, body, msg_id, in_reply_to=""):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = "arun.replytest@gmail.example"
    m["Subject"] = subject
    m["Date"] = "Mon, 12 Oct 2026 10:00:00 +0000"
    m["Message-ID"] = msg_id
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
        m["References"] = in_reply_to
    m.set_content(body)
    INBOX[str(mid_num).encode()] = m.as_bytes()


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def select(self, box, readonly=False):
        selected.append((box, readonly))
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        searches.append(criteria)
        ids = [k for k, raw in INBOX.items() if f'"{email.message_from_bytes(raw)["From"].split("<")[-1].strip(">")}"' in criteria]
        return "OK", [b" ".join(ids)]

    def fetch(self, mid, what):
        fetches.append(what)
        return "OK", [(mid + b" (BODY[] {1}", INBOX[mid]), b")"]

    def append(self, folder, flags, when, raw):
        appended.append((folder, email.message_from_bytes(raw, policy=email.policy.default)))

    def logout(self):
        pass


class FakeSMTP:
    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        pass

    def send_message(self, msg):
        sent.append(msg)


ri.imaplib.IMAP4_SSL = FakeIMAP      # the one imaplib module - drafts and reading both use the stub
ri.smtplib.SMTP_SSL = FakeSMTP
resume_bot.ai_configured = lambda: True
resume_bot.gemini_configured = lambda: False
ai_draft = {"body": None}


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    if system == ri.CLASSIFY_SYSTEM:
        low = prompt.lower()
        cat = ("auto_reply" if "out of office" in low else "interview" if "interview" in low else "rejection" if "filled" in low
               else "needs_resume" if "send your resume" in low else "question")
        return json.dumps({"category": cat, "reason": f"looks like {cat}"}), False, "stub-model"
    body = ai_draft["body"] or "Hi,\n\nThank you for your email. Please find the details below.\n\nBest regards,\nArun Replytest\n+1 469 555 0102 | arun.replytest@gmail.example"
    return json.dumps({"body": body, "needs_info": [], "reply": "Drafted a reply."}), False, "stub-model"


resume_bot._generate = fake_generate

uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
EMAIL, PW = "reply-rec@example.invalid", "Reply-1"
REC = {"id": uid(models.create_user("Ravi Replytest", EMAIL, PW, role="Recruiter")), "name": "Ravi Replytest", "role": "Recruiter"}
OTHER = {"id": uid(models.create_user("Other Replytest", "reply-other@example.invalid", "Reply-2", role="Recruiter")), "name": "Other", "role": "Recruiter"}
cid = models.create_candidate("Arun Replytest", "arun.replytest@gmail.example", phone="+1 469 555 0102", title="DevOps Engineer",
                              primary_skills="AWS, Terraform", experience_years=8, target_rate="$70/hr", visa_status="H1B",
                              assigned_user_id=REC["id"])
models.update_candidate(cid, gmail_account="arun.replytest@gmail.example", gmail_app_password="fake app pass not real")
models.save_resume_file(cid, "Arun_Resume.docx", b"PK fake resume")


def job(title, url):
    j = models.save_or_update_scraped_job({"title": title, "company": "Acme Staffing", "source": "Dice", "recruiter_email": REC_ADDR,
                                           "url": url, "description": "DevOps AWS Terraform"})
    return j["id"] if isinstance(j, dict) else j


j1 = job("DevOps Engineer Replytest", "https://www.dice.com/job-detail/rp-1")
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {**REC, "email": EMAIL}

# ---- the draft carries a Message-ID kept on the application
r = client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": j1}).get_json()
check(r.get("success"), f"draft created: {r}")
draft_mid = appended[-1][1]["Message-ID"] if appended else ""
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT id, outreach_message_id, stage FROM applications WHERE job_id = ? AND candidate_id = ?", (j1, cid))
app_row = cur.fetchone()
conn.close()
app_id = app_row["id"]
check(draft_mid and app_row["outreach_message_id"] == draft_mid, f"Message-ID kept: {draft_mid} / {app_row['outreach_message_id']}")

# ---- recruiter mail arrives
mail(1, f"Priya <{REC_ADDR}>", "Re: Application: DevOps Engineer", "Hi Arun,\nCan we schedule an interview this week?\n\nOn Mon, Oct 12, 2026 Arun wrote:\n> my old email text\n> more", "<m1@acme>", draft_mid)
mail(2, f"Priya <{REC_ADDR}>", "Automatic reply", "I am out of office until Friday.", "<m2@acme>")
mail(3, "Stranger <someone@else.example>", "Hello", "Not about this application", "<m3@else>")
d = client.post("/api/applications/check-replies", json={"candidate_id": cid}).get_json()
check(d.get("success") and d["new_replies"] == 2, f"two recruiter mails read: {d}")
check(all(ro for _, ro in selected) and fetches and all("PEEK" in f for f in fetches), "INBOX read-only and BODY.PEEK (stays unread)")
check(all('"someone@else.example"' not in c for c in searches), "only the recruiter's address is searched")
check(client.post("/api/applications/check-replies", json={"candidate_id": cid}).get_json()["new_replies"] == 0, "second check adds nothing")
conn = models.get_db_connection()
check(ri._claim(conn, app_id, cid, ri.parse_message(INBOX[b"1"])) is None, "same message claimed twice -> second is skipped (two workers)")
conn.close()

lst = client.get(f"/api/replies?candidate_id={cid}").get_json()
by_cat = {x["category"]: x for x in lst["replies"]}
check(set(by_cat) == {"interview", "auto_reply"} and lst["mode"] == "draft", f"sorted: {list(by_cat)} mode={lst.get('mode')}")
iv = client.get(f"/api/replies/{by_cat['interview']['id']}").get_json()
check("schedule an interview" in iv["body"] and "old email text" not in iv["body"] and "wrote:" not in iv["body"], f"quoted text stripped: {iv['body']!r}")
check(iv["draft_body"] and "Arun Replytest" in iv["draft_body"], "reply prepared for the interview request")
check(by_cat["auto_reply"]["handled"] == 1 and not by_cat["auto_reply"]["has_draft"], "auto-reply: handled, no draft")
apps = client.get(f"/api/applications?candidate_id={cid}").get_json()["applications"]
a1 = next(a for a in apps if a["id"] == app_id)
check(a1["stage"] == "Interviewing" and a1["reply_count"] == 1 and a1["replies_open"] == 1, f"status Interview, badge counts: {a1['stage']} {a1['reply_count']} {a1['replies_open']}")
check(len(client.get(f"/api/replies?candidate_id={cid}&open=1").get_json()["replies"]) == 1, "open filter")

# ---- AI chat on the reply; invented rate refused
ai_draft["body"] = "Hi Priya,\n\nHe is available Tuesday 2-5 PM IST for a call.\n\nBest regards,\nArun Replytest"
out = client.post(f"/api/replies/{iv['id']}/ai", json={"instruction": "Say he can talk Tuesday 2-5 PM IST", "current_body": iv["draft_body"]}).get_json()
check(out["applied"] and "Tuesday" in out["body"], f"AI edit applied: {out}")
ai_draft["body"] = "Hi Priya,\n\nHis rate is $95/hr.\n\nBest regards,\nArun Replytest"
out = client.post(f"/api/replies/{iv['id']}/ai", json={"instruction": "answer her", "current_body": "x"}).get_json()
check(not out["applied"] and "$95/hr" in out["note"] and out["body"] == "x", f"invented rate refused: {out}")
ai_draft["body"] = None

# ---- save in the same Gmail thread
n_before = len(appended)
r = client.post(f"/api/replies/{iv['id']}/save-draft", json={"body": "Hi Priya,\n\nTuesday 2-5 PM IST works.\n\nArun", "attach_resume": True}).get_json()
check(r.get("success") and r["attached"] == "Arun_Resume.docx", f"saved: {r}")
folder, m = appended[-1] if len(appended) > n_before else (None, None)
check(m is not None and folder == '"[Gmail]/Drafts"' and m["To"] == REC_ADDR and m["Subject"].startswith("Re: ") and m["In-Reply-To"] == "<m1@acme>"
      and "<m1@acme>" in m["References"] and draft_mid in m["References"], f"threaded draft headers: {m and dict(m.items())}")
check(m is not None and any(p.get_filename() == "Arun_Resume.docx" for p in m.iter_attachments()), "resume attached")
check(client.get(f"/api/replies/{iv['id']}").get_json()["handled"] == 1, "saved -> handled")
check(not sent, "nothing sent in draft mode")

# ---- send_safe: only resume requests go out automatically
check(client.post("/api/me/auto-reply-mode", json={"mode": "send_safe"}).get_json()["mode"] == "send_safe", "mode saved")
check(client.post("/api/me/auto-reply-mode", json={"mode": "yolo"}).status_code == 400, "unknown mode refused")
j2 = job("SRE Replytest", "https://www.dice.com/job-detail/rp-2")
client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": j2})
REC_ADDR_SAVE = REC_ADDR
mail(4, f"Priya <{REC_ADDR}>", "Re: SRE", "Thanks - please send your resume in Word format.", "<m4@acme>")
mail(5, f"Priya <{REC_ADDR}>", "Re: SRE", "Another question: what is his notice period?", "<m5@acme>")
client.post("/api/applications/check-replies", json={"candidate_id": cid})
check(len(sent) == 1 and sent[0]["In-Reply-To"] == "<m4@acme>" and any(p.get_filename() == "Arun_Resume.docx" for p in sent[0].iter_attachments()),
      f"resume request answered automatically with the resume: {len(sent)}")
q = next(x for x in client.get(f"/api/replies?candidate_id={cid}").get_json()["replies"] if x["subject"] == "Re: SRE" and x["category"] == "question")
check(not q["sent_at"] and q["has_draft"], "a question is prepared, never sent automatically")

# ---- rejection; mode off prepares nothing
client.post("/api/me/auto-reply-mode", json={"mode": "off"})
j3 = job("Cloud Replytest", "https://www.dice.com/job-detail/rp-3")
client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": j3})
INBOX.clear()
mail(6, f"Priya <{REC_ADDR}>", "Re: Cloud", "Sorry, the position has been filled.", "<m6@acme>")
mail(7, f"Priya <{REC_ADDR}>", "Re: Cloud", "Quick interview tomorrow?", "<m7@acme>")
client.post("/api/applications/check-replies", json={"candidate_id": cid})
rows = {x["category"]: x for x in client.get(f"/api/replies?candidate_id={cid}").get_json()["replies"] if x["subject"] == "Re: Cloud"}
check(rows.get("rejection", {}).get("handled") == 1, "rejection handled")
check(not rows.get("interview", {}).get("has_draft"), "mode off: no reply prepared")

# ---- privacy
with client.session_transaction() as s:
    s["user"] = {**OTHER, "email": "reply-other@example.invalid"}
check(client.get(f"/api/replies/{iv['id']}").status_code == 404 and client.get("/api/replies").get_json()["replies"] == [], "other recruiters see nothing")

# ---- the screens (Edge)
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
shot = os.path.join(tmp_dir, "replies.png")
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
        check("app.js?v=5.76.0" in page.content(), "cache-buster not bumped to 5.76.0")
        page.wait_for_function("typeof state !== 'undefined' && state.consultants && state.consultants.length", timeout=15000)
        page.evaluate(f"openTracker({cid})")
        page.wait_for_selector(f"tr[data-app-id='{app_id}'] .trk-rp", timeout=10000)
        page.click(f"tr[data-app-id='{app_id}'] .trk-rp")
        page.wait_for_selector("#rp-draft", timeout=10000)
        check("schedule an interview" in page.inner_text("#rp-body") and page.is_visible("#modal-replies"), "badge opens that application's reply")
        check(page.input_value("#rp-mode") == "off", "my auto-reply setting shown")
        page.fill("#rp-draft", "")
        page.click(".rp-chip:has-text('Write a reply')")
        page.wait_for_function("document.getElementById('rp-draft').value.includes('Thank you for your email')", timeout=10000)
        page.click("#rp-undo")
        check(page.input_value("#rp-draft") == "", "Undo")
        page.fill("#rp-draft", "Hi Priya, Tuesday works. Arun")
        page.click("#rp-save")
        page.wait_for_function("document.body.innerText.includes('saved in Gmail Drafts')", timeout=10000)
        page.select_option("#rp-mode", "draft")
        page.wait_for_timeout(500)
        page.click("#rp-check")
        page.wait_for_timeout(800)
        check(page.input_value("#rp-mode") == "draft", "the setting stays as chosen after the list refreshes")
        page.screenshot(path=shot)
        browser.close()
finally:
    server.shutdown()
conn = models.get_db_connection()
check(ri.owner_mode(conn, {"assigned_user_id": REC["id"]}) == "draft", "mode changed from the screen")
conn.close()
check(not js_errors, f"JavaScript errors: {js_errors}")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: recruiter replies - Message-ID kept, read-only PEEK, recruiter-only, quotes stripped, dedupe, AI sorting + status, facts-only drafts, threaded Gmail draft + resume, send_safe only for resume requests, mode off, privacy, screens. Screenshot: {shot}")
