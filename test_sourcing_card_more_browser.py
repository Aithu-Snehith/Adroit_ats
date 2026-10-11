"""Browser test (Playwright + Microsoft Edge): the sourcing card's extra details.

Open a candidate's card, fill visa expiry, current rate, experience, work mode, primary skills,
preferred locations, certifications, GitHub, portfolio, referred by and marital status; Save; the
values come back (links normalised, with "open" links); attach a resume (.docx), download link
shown, remove it; the card still fits a phone-width screen. No network beyond the local app.
People are labelled test fixtures.
Run: python test_sourcing_card_more_browser.py
"""
import io
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_card_more_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import docx  # noqa: E402
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "card-more-ui@example.invalid", "CardMore-Test-1"
models.create_user("Latha Moreui", EMAIL, PASSWORD, role="Recruiter")
li.ingest_profiles(li.ApifyProfileProvider().to_profiles([{
    "linkedinUrl": "https://www.linkedin.com/in/card-more-1.example.invalid", "firstName": "Gopi", "lastName": "Moreui",
    "headline": "DevOps Engineer", "location": {"linkedinText": "Austin, Texas"}, "currentPosition": [],
    "education": [{"schoolName": "CBIT", "degree": "B.Tech", "endDate": {"year": 2019}}]}]), "apify", None)
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT id FROM linkedin_profiles WHERE linkedin_url LIKE '%card-more-1%'")
PID = cur.fetchone()["id"]
conn.close()
resume_path = os.path.join(tmp_dir, "Gopi_Resume.docx")
d = docx.Document()
d.add_paragraph("GOPI MOREUI - DevOps Engineer")
d.save(resume_path)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
OPEN = "document.getElementById('trk-drawer').style.transform === 'translateX(0px)'"
shot = os.path.join(tmp_dir, "card_more.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda dlg: dlg.accept())
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.75.0" in page.content(), "cache-buster not bumped to 5.75.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.evaluate(f"trkOpen({PID})")
        page.wait_for_function(OPEN)
        for sel in ("#trk-visa-expiry", "#trk-current-rate", "#trk-experience", "#trk-work-mode", "#trk-skills", "#trk-pref-locations",
                    "#trk-certs", "#trk-github", "#trk-portfolio", "#trk-referred", "#trk-marital", "#trk-resume"):
            check(page.locator(sel).count() == 1, f"card has {sel}")

        page.fill("#trk-visa-expiry", "2027-03-31")
        page.fill("#trk-current-rate", "$50/hr W2")
        page.fill("#trk-experience", "6")
        page.select_option("#trk-work-mode", "Remote")
        page.fill("#trk-skills", "Terraform, Kubernetes, AWS")
        page.fill("#trk-pref-locations", "Austin, Remote")
        page.fill("#trk-certs", "CKA")
        page.fill("#trk-github", "github.com/gopi-moreui")
        page.fill("#trk-portfolio", "gopi.example.dev")
        page.fill("#trk-referred", "Dice")
        page.select_option("#trk-marital", "Single")
        page.click("#trk-save")
        page.wait_for_function("document.getElementById('trk-activity').innerText.includes('GitHub')")
        check(page.input_value("#trk-github") == "https://github.com/gopi-moreui", f"GitHub normalised: {page.input_value('#trk-github')}")
        check(page.locator("label[for=trk-github] a").get_attribute("href") == "https://github.com/gopi-moreui", "GitHub open link")
        check(page.input_value("#trk-work-mode") == "Remote" and page.input_value("#trk-marital") == "Single" and page.input_value("#trk-visa-expiry") == "2027-03-31", "dropdowns / date kept")
        page.evaluate(f"trkClose(); trkOpen({PID})")
        page.wait_for_function(OPEN)
        page.wait_for_function("document.getElementById('trk-skills') && document.getElementById('trk-skills').value === 'Terraform, Kubernetes, AWS'")

        # bad GitHub link -> message
        page.fill("#trk-github", "gitlab.com/gopi")
        page.click("#trk-save")
        page.wait_for_function("document.body.innerText.includes('github.com/username')", timeout=5000)
        page.fill("#trk-github", "https://github.com/gopi-moreui")

        # resume
        page.set_input_files("#trk-resume", resume_path)
        page.wait_for_selector("#trk-resume-current a", timeout=10000)
        check(page.inner_text("#trk-resume-current a") == "Gopi_Resume.docx", "resume name shown")
        check(page.get_attribute("#trk-resume-current a", "href") == f"/api/sourcing/profiles/{PID}/resume", "download link")
        page.screenshot(path=shot)
        page.click("#trk-resume-del")
        page.wait_for_function("!document.getElementById('trk-resume-current')", timeout=10000)

        # phone width
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        w = page.evaluate("[document.getElementById('trk-drawer').scrollWidth, document.getElementById('trk-drawer').clientWidth]")
        check(w[0] <= w[1] + 1, f"card fits phone width: {w}")
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
print(f"PASS: card extra details in Edge - fields saved and reloaded, links normalised + open, bad GitHub refused, resume attach/download/remove, phone width. Screenshot: {shot}")
