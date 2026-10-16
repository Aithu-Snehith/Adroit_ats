"""Resume workspace in the Optimize window: preview, Find, JD keywords, Edit-with-AI chat, Undo, Save.

1. resume_editor.edit (AI stubbed): an edit is applied to the Word file itself; a heading changes only
   when asked; a technology or a figure not in the resume is refused unless the instruction names it;
   lines are removed only when asked; years of experience never change.
2. Edge: optimize from Paste Requirement & Draft -> the Word file is previewed with the changed line
   highlighted; Find counts and steps; the JD keywords strip counts; clicking a line puts it in the chat;
   a chat edit shows in the preview, Undo takes it back, Save stores the chat-edited Word file.
3. Edge, Resume Optimizer tab: the JD is read from a job link; the same workspace (preview, Find,
   keywords, chat) works there and "Save version" stores the chat-edited Word file.
The AI and Gmail are never contacted (stubs). Labelled test fixtures.
Run: python test_resume_editor_browser.py
"""
import base64
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_resume_editor.db")
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
import job_description_fetch  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402
import outreach_ai  # noqa: E402
import resume_editor  # noqa: E402
import resume_versions as rv  # noqa: E402
import us_job_scrapers  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        pass

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP
fetched_urls = []


def fake_fetch(url):
    fetched_urls.append(url)
    if "unreadable" in url:
        return None, "That site blocks automatic reading - paste the JD instead."
    return JD, ""


job_description_fetch.fetch_full_description = fake_fetch
failures, js_errors, ai_calls = [], [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


LINES = ["KOUSHIK EDITORUI", "Java Developer", "PROFESSIONAL SUMMARY", "Java developer with 6 years of experience.",
         "Built REST APIs for retail order systems.", "TECHNICAL SKILLS", "Java, Spring Boot"]
d = docx.Document()
for line in LINES:
    d.add_paragraph(line)
buf = io.BytesIO()
d.save(buf)
DOCX = buf.getvalue()

ANALYSIS = {"initial_match_percentage": 72, "match_breakdown": {"mandatory_skills": 29, "recent_project_relevance": 18, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Retail",
            "strong_match_skills": ["Java", "Spring Boot"], "mandatory_matched_skills": ["Java", "Spring Boot"], "partial_match_skills": [],
            "mandatory_missing_skills": ["React"], "preferred_missing_skills": [], "risky_skills_avoided": [],
            "skills_to_add": {"summary": [], "technical_skills": ["React"], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 84}
EDIT_PLAN = {"fn": None}


def line_no(prompt, text):
    m = re.search(r"^\[(\d+)\][^\n]*?" + re.escape(text), prompt, re.M)
    return int(m.group(1))


PROMPTS = {"resume": [], "email": []}


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    if system == resume_editor.SYSTEM:
        ai_calls.append("edit")
        PROMPTS["resume"].append(prompt)
        return json.dumps(EDIT_PLAN["fn"](prompt)), False, "stub"
    if system == outreach_ai.SYSTEM:
        PROMPTS["email"].append(prompt)
        if "REVIEW:" in prompt:
            return json.dumps({"reply": "Good overall, but it doesn't say where you found the role.",
                               "suggestions": [{"label": "Say we found it on LinkedIn", "instruction": "Say we came across the posting on LinkedIn"}]}), False, "stub"
        cur_subject = prompt.split("CURRENT SUBJECT:\n", 1)[1].split("\n\nCURRENT EMAIL:", 1)[0]
        cur_body = prompt.split("CURRENT EMAIL:\n", 1)[1].split("\n\nRECRUITER'S MESSAGE:", 1)[0]
        return json.dumps({"subject": cur_subject, "body": cur_body + "\nI came across this posting on LinkedIn.",
                           "changes": ["Added where the role was found"], "reply": "Added that you found it on LinkedIn.",
                           "suggestions": [{"label": "Shorter opening", "instruction": "Make the opening line shorter"}]}), False, "stub"
    if not want_json:
        ai_calls.append("rewrite")
        return f"[{line_no(prompt, 'Java, Spring Boot')}] Java, Spring Boot, React", False, "stub"
    ai_calls.append("analysis" if rb._ANALYSIS_CONTRACT in system else "other")
    return json.dumps(ANALYSIS if rb._ANALYSIS_CONTRACT in system else {}), False, "stub"


rb._generate = fake_generate
rb.gemini_configured = lambda: True
rb.ai_configured = lambda: True


def plan(*edits, reply="Done."):
    return lambda prompt: {"edits": [e(prompt) for e in edits], "reply": reply, "needs_info": []}


def rep(target, new):
    return lambda prompt: {"op": "replace", "paragraph": line_no(prompt, target), "new_text": new}


def texts(b64):
    return docx_editor.extract_text(base64.b64decode(b64))


# ---- 1. the guards
EDIT_PLAN["fn"] = plan(rep("Java Developer", "Java Full Stack Developer"))
out = resume_editor.edit(DOCX, "Change the headline to Java Full Stack Developer")
check(out["changed"] and "Java Full Stack Developer" in texts(out["docx_base64"]), f"headline edit applied to the Word file: {out}")

EDIT_PLAN["fn"] = plan(rep("PROFESSIONAL SUMMARY", "CAREER SUMMARY"))
out = resume_editor.edit(DOCX, "Make the summary sound better")
check(not out["changed"] and "Refused" in out["reply"], f"a heading is not changed when not asked: {out['reply']}")
out = resume_editor.edit(DOCX, "Rename the summary heading to CAREER SUMMARY")
check(out["changed"], f"a heading changes when asked: {out['reply']}")

EDIT_PLAN["fn"] = plan(rep("Built REST APIs", "Built REST APIs on Kubernetes for retail order systems."))
out = resume_editor.edit(DOCX, "Make the project line stronger")
check(not out["changed"] and "Kubernetes" in out["reply"], f"a new technology is refused: {out['reply']}")
out = resume_editor.edit(DOCX, "Mention Kubernetes in the project line, he deployed the APIs on it")
check(out["changed"] and "Kubernetes" in out["text"], f"a technology named in the instruction is allowed: {out['reply']}")

EDIT_PLAN["fn"] = plan(rep("Built REST APIs", "Built REST APIs for retail order systems, cutting latency by 40%."))
out = resume_editor.edit(DOCX, "Make the project line stronger")
check(not out["changed"], f"an invented figure is refused: {out['reply']}")

EDIT_PLAN["fn"] = plan(lambda p: {"op": "delete", "paragraph": line_no(p, "Built REST APIs")})
out = resume_editor.edit(DOCX, "Make the project line stronger")
check(not out["changed"], f"a line is not removed unless asked: {out['reply']}")
out = resume_editor.edit(DOCX, "Remove the REST APIs line")
check(out["changed"] and "Built REST APIs" not in out["text"], f"a line is removed when asked: {out['reply']}")

EDIT_PLAN["fn"] = plan(rep("Java developer with 6 years", "Java developer with 8 years of experience."))
out = resume_editor.edit(DOCX, "Say 8 years in the summary")
check(not out["changed"], f"years of experience never change: {out['reply']}")

EDIT_PLAN["fn"] = plan(rep("Java Developer", "Java Full Stack Developer"), reply="Headline updated to the JD title.")
HEADLINE_PLAN = EDIT_PLAN["fn"]


def review_or_edit(prompt):
    if "REVIEW:" in prompt:      # advice only: no edits, a suggestion to apply
        return {"edits": [], "reply": "The headline says Java Developer but this JD is for a Java Full Stack Developer.",
                "suggestions": [{"label": "Headline = JD title", "instruction": "Change the headline to Java Full Stack Developer"}], "needs_info": []}
    return HEADLINE_PLAN(prompt)


# ---- 1b. the chats as assistants: advice without edits, suggestions, memory, the chat model
EDIT_PLAN["fn"] = review_or_edit
out = resume_editor.edit(DOCX, "__review__", history=[{"role": "user", "text": "We found this on LinkedIn"}])
check(not out["changed"] and out["suggestions"] and "headline" in out["reply"].lower(), f"Review & suggest: advice, no edits: {out}")
check("CONVERSATION SO FAR" in PROMPTS["resume"][-1] and "found this on LinkedIn" in PROMPTS["resume"][-1], "the conversation is sent with the message")
EDIT_PLAN["fn"] = plan(rep("Built REST APIs", "Built REST APIs on Kubernetes for retail order systems."))
out = resume_editor.edit(DOCX, "Mention it in the project line", history=[{"role": "user", "text": "He deployed the APIs on Kubernetes"}])
check(out["changed"] and "Kubernetes" in out["text"], f"a fact the recruiter stated earlier in the chat counts as confirmed: {out['reply']}")
EDIT_PLAN["fn"] = review_or_edit
chat_models = []
real_claude, real_conf = rb._generate_claude, rb.claude_configured
rb.claude_configured = lambda: True
rb._generate_claude = lambda system, prompt, want_json, max_tokens, model="", effort="": (chat_models.append(model) or (json.dumps({"reply": "ok"}), False, model))
rb.generate_chat("s", "p")
check(chat_models == ["claude-sonnet-5-5"], f"the chats use the chat model (Sonnet 5.5): {chat_models}")
def _fail(*a, **k):
    raise rb.ClaudeError("down")
rb._generate_claude = _fail
check(rb.generate_chat("s", "p")[1] == "stub", "the chat falls back to the normal chain when the chat model fails")
rb._generate_claude, rb.claude_configured = real_claude, real_conf

# ---- 2. the screen
EMAIL, PASSWORD = "rveditor@example.invalid", "RvEditor-Test-1"
admin = models.create_user("Resume Editor Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
cid = models.create_candidate("Koushik Editorui", "koushik-editorui@example.invalid", title="Java Developer", primary_skills="Java, Spring Boot",
                              gmail_account="koushik-editorui@example.invalid", assigned_user_id=admin_id)
models.update_candidate(cid, gmail_app_password="test-app-password-not-real", country="United States")
models.save_resume_file(cid, "Koushik_original.docx", DOCX)
JD = "Java Full Stack Developer\nRequired: Java, Spring Boot and React.\nSend resumes to rec@example.invalid"

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
shot = os.path.join(tmp_dir, "resume_editor.png")

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
        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to 5.88.0")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)

        page.click("#btn-open-paste-draft-modal")
        page.select_option("#pd-consultant-select", str(cid))
        page.fill("#pd-raw-text", JD)
        page.click("#btn-pd-optimize")
        page.wait_for_function("!document.getElementById('btn-do-save').disabled", timeout=20000)
        page.wait_for_function("document.querySelector('#rv-preview .docx-wrapper') && document.querySelector('#rv-preview').innerText.includes('React')", timeout=15000)
        check(page.is_visible("#rv-pane-preview") and not page.is_visible("#do-optimized"), "the preview tab is shown first")
        changed = page.eval_on_selector_all("#rv-preview .rv-changed", "els => els.map(e => e.textContent)")
        check(changed == ["Java, Spring Boot, React"], f"only the changed line is highlighted: {changed}")
        page.uncheck("#rv-show-changes")
        check(page.locator("#rv-preview .rv-changed").count() == 0, "highlight can be switched off")
        page.check("#rv-show-changes")

        kw = page.inner_text("#rv-kw")
        check("Java 3" in kw and "Spring Boot 1" in kw and "React 1" in kw, f"JD keyword counts: {kw!r}")
        page.fill("#rv-find", "java")
        page.wait_for_function("document.getElementById('rv-find-count').innerText.includes('of')", timeout=5000)
        n = page.locator("#rv-preview mark.rv-find").count()
        check(n == 3 and page.inner_text("#rv-find-count") == "1 of 3", f"Find counts every match: {n}, {page.inner_text('#rv-find-count')}")
        page.click("#rv-find-next")
        check(page.inner_text("#rv-find-count") == "2 of 3", "Find steps to the next match")
        page.click("#rv-find-prev")
        page.click("#rv-find-prev")
        check(page.inner_text("#rv-find-count") == "3 of 3", "Find wraps around backwards")
        page.click("#rv-kw .rv-kwbtn[data-k='React']")
        check(page.input_value("#rv-find") == "React" and page.inner_text("#rv-find-count") == "1 of 1", "clicking a keyword finds it")
        page.fill("#rv-find", "")
        page.wait_for_function("document.getElementById('rv-find-count').innerText === ''", timeout=5000)

        page.click("#rv-preview p:has-text('Built REST APIs')")
        check(page.input_value("#rv-instr").startswith('In the line "Built REST APIs'), "clicking a line puts it in the chat box")

        page.click("#rv-fullscreen")
        check("rv-full" in page.get_attribute("#rv-workspace", "class") and page.evaluate("getComputedStyle(document.getElementById('rv-workspace')).position") == "fixed", "full screen toggles")
        page.click("#rv-fullscreen")

        ai_calls.clear()
        page.fill("#rv-instr", "Change the headline to Java Full Stack Developer")
        page.click("#rv-send")
        page.wait_for_function("document.querySelector('#rv-preview').innerText.includes('Java Full Stack Developer')", timeout=15000)
        # count only the chat's calls: the Paste window's own AI reading of the requirement can finish late
        check(ai_calls.count("edit") == 1 and "rewrite" not in ai_calls, f"one AI call for the chat edit: {ai_calls}")
        check("Headline updated" in page.inner_text("#rv-chat-msgs"), "the chat answers what it changed")
        check("Java Full Stack Developer" in page.input_value("#do-optimized"), "the Text tab follows the chat edit")
        check(not page.is_disabled("#rv-undo"), "Undo is available")
        page.click("#rv-undo")
        page.wait_for_function("!document.querySelector('#rv-preview').innerText.includes('Java Full Stack Developer')", timeout=10000)
        check(page.is_disabled("#rv-undo") and "Java Full Stack Developer" not in page.input_value("#do-optimized"), "Undo goes back")

        # ---- the resume chat as an assistant: Review & suggest (no edits) -> Apply a suggestion; it remembers
        EDIT_PLAN["fn"] = review_or_edit
        before = page.input_value("#do-optimized")
        page.click("#rv-workspace .rv-chip[data-instr='__review__']")
        page.wait_for_function("document.getElementById('rv-chat-msgs').innerText.includes('suggest next')", timeout=15000)
        msgs = page.inner_text("#rv-chat-msgs")
        check("Review & suggest" in msgs and "this JD is for a Java Full Stack Developer" in msgs and page.input_value("#do-optimized") == before,
              f"advice shown, resume unchanged: {msgs[-300:]!r}")
        page.click("#rv-chat-msgs .rv-sugg")
        page.wait_for_function("document.querySelector('#rv-preview').innerText.includes('Java Full Stack Developer')", timeout=15000)
        check("CONVERSATION SO FAR" in PROMPTS["resume"][-1] and "Review & suggest" in PROMPTS["resume"][-1], "Apply sends the conversation too")
        page.click("#rv-undo")
        page.wait_for_function("!document.querySelector('#rv-preview').innerText.includes('Java Full Stack Developer')", timeout=10000)

        # ---- the email chat: Review & suggest, Apply, the conversation shown and remembered
        page.wait_for_function("document.getElementById('do-email-body') && document.getElementById('do-email-body').value", timeout=15000)
        page.click("#btn-do-email-review")
        page.wait_for_function("document.getElementById('do-email-chat').innerText.includes('where you found the role')", timeout=15000)
        check(page.is_visible("#do-email-chat") and "LinkedIn" not in page.input_value("#do-email-body"), "email advice shown, email unchanged")
        page.click("#do-email-chat .em-sugg")
        page.wait_for_function("document.getElementById('do-email-body').value.includes('posting on LinkedIn')", timeout=15000)
        check("CONVERSATION SO FAR" in PROMPTS["email"][-1] and "where you found the role" in PROMPTS["email"][-1], "the email chat remembers its conversation")
        chat_text = page.inner_text("#do-email-chat")
        check(chat_text.count("Review & suggest") == 1 and "Added that you found it on LinkedIn" in chat_text and "Shorter opening" in chat_text,
              f"conversation + next suggestion shown: {chat_text!r}")

        page.locator("#do-email-box").scroll_into_view_if_needed()
        page.screenshot(path=shot.replace(".png", "_chat.png"))
        # closing and reopening the window for the same requirement brings both conversations back
        page.click("#btn-close-draft-optimize")
        page.click("#btn-pd-optimize")
        page.wait_for_function("document.getElementById('rv-chat-msgs').innerText.includes('Review & suggest')", timeout=15000)
        check("this JD is for a Java Full Stack Developer" in page.inner_text("#rv-chat-msgs"), "resume chat remembered after reopening")
        EDIT_PLAN["fn"] = HEADLINE_PLAN

        page.click("#rv-workspace .rv-chip[data-instr*='eadline']")
        page.wait_for_function("document.querySelector('#rv-preview').innerText.includes('Java Full Stack Developer')", timeout=15000)
        page.screenshot(path=shot)
        page.click("#btn-do-save")
        page.wait_for_function("document.getElementById('pd-resume-version').innerText.includes('.docx')", timeout=10000)
        saved = rv.list_for(cid)
        check(len(saved) == 1, f"one version saved: {saved}")
        if saved:
            v = rv.get(saved[0]["id"])
            body = docx_editor.extract_text(v["data"])
            check("Java Full Stack Developer" in body and "Java, Spring Boot, React" in body, f"the saved Word file has the chat edit: {body!r}")
        page.click("#btn-close-draft-optimize") if page.is_visible("#btn-close-draft-optimize") else None
        page.click("#btn-close-paste-draft-modal") if page.is_visible("#btn-close-paste-draft-modal") else None

        # ---- 3. Resume Optimizer tab
        page.goto(f"http://127.0.0.1:{port}/dashboard#resumebot")
        page.wait_for_function("state.consultants && state.consultants.length >= 1", timeout=15000)
        page.click("a.nav-item[data-tab=resumebot]")
        tab = page.inner_text("#tab-resumebot")
        check("AI: Claude" in tab or "AI: Google Gemini" in tab, "one-line AI note in the header")
        check("falls back" not in tab and "free tier" not in tab, "the long AI paragraph is gone")
        page.select_option("#resumebot-consultant-select", str(cid))
        page.wait_for_function("document.getElementById('resumebot-resume-text').value.includes('KOUSHIK EDITORUI')", timeout=10000)

        page.fill("#resumebot-jd-url", "https://careers.unreadable.example.invalid/job/9")
        page.click("#btn-resumebot-read-jd")
        page.wait_for_selector("text=blocks automatic reading", timeout=10000)
        check(page.input_value("#resumebot-jd-text") == "", "an unreadable link leaves the JD box empty (nothing invented)")
        page.fill("#resumebot-jd-url", "https://careers.example.invalid/job/1")
        page.click("#btn-resumebot-read-jd")
        page.wait_for_function("document.getElementById('resumebot-jd-text').value.startsWith('Java Full Stack Developer')", timeout=10000)
        check(fetched_urls[-1] == "https://careers.example.invalid/job/1", f"the link was read: {fetched_urls}")

        page.click("#btn-run-resume-optimization")
        page.wait_for_function("document.querySelector('#rbw-preview .docx-wrapper') && document.querySelector('#rbw-preview').innerText.includes('React')", timeout=20000)
        changed = page.eval_on_selector_all("#rbw-preview .rv-changed", "els => els.map(e => e.textContent)")
        check(changed == ["Java, Spring Boot, React"], f"tab: changed line highlighted: {changed}")
        check("React 1" in page.inner_text("#rbw-kw"), "tab: keyword counts")
        page.fill("#rbw-find", "java")
        page.wait_for_function("document.getElementById('rbw-find-count').innerText === '1 of 3'", timeout=5000)
        page.fill("#rbw-instr", "Change the headline to Java Full Stack Developer")
        page.click("#rbw-send")
        page.wait_for_function("document.querySelector('#rbw-preview').innerText.includes('Java Full Stack Developer')", timeout=15000)
        check("Java Full Stack Developer" in page.input_value("#result-preview-text"), "tab: the Text tab follows the chat edit")
        page.screenshot(path=shot.replace(".png", "_tab.png"), full_page=False)
        page.click("#btn-save-optimized-version")
        page.wait_for_function("document.getElementById('save-version-status').innerText.includes('Saved as')", timeout=10000)
        saved = rv.list_for(cid)
        check(len(saved) == 2, f"tab: second version saved: {saved}")
        if len(saved) == 2:
            newest = max(saved, key=lambda x: x["id"])
            body = docx_editor.extract_text(rv.get(newest["id"])["data"])
            check("Java Full Stack Developer" in body and "Java, Spring Boot, React" in body, f"tab: saved Word file has the chat edit: {body!r}")
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JS errors: {js_errors}")
if failures:
    print("FAIL")
    for f in failures:
        print(" -", f)
    print("screenshot:", shot)
    sys.exit(1)
print("PASS resume editor: guards, preview, highlight, Find, keywords, chat edit, Undo, Save - Optimize window and Resume Optimizer tab (JD from link)")
