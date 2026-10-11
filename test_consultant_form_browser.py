"""Browser test (Playwright + Microsoft Edge): the Add / Edit Consultant form by country.

- India: no visa / relocation question (hidden, not required, nothing saved); the rate box is an
  "Expected Salary (CTC)" (India has salaries, not $/hr C2C rates).
- United States: one option per real status - H1B and H1B Transfer, OPT, STEM OPT and CPT are
  separate; L2 and B1/B2 exist.
- No invented values: no "$90/hr (C2C)" pre-filled, no "C2C Eligible" saved or shown when empty.
- An older saved value ("OPT/CPT") is kept as "(previously saved)".
People are labelled test fixtures. No network beyond the local app.
Run: python test_consultant_form_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_cform.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "cform-admin@example.invalid", "Cform-Test-1"
admin = models.create_user("Cform Admin", EMAIL, PASSWORD, role="Admin")
admin_id = admin["id"] if isinstance(admin, dict) else admin
old_id = models.create_candidate("Oldvisa Formtest", "oldvisa@example.invalid", title="Java Developer", visa_status="OPT/CPT",
                                 country="United States", target_rate="$60/hr", assigned_user_id=admin_id)

# API: nothing invented when visa / rate are left out
client = app_module.app.test_client()
with client.session_transaction() as s:
    s["user"] = {"id": admin_id, "name": "Cform Admin", "role": "Admin", "email": "a@example.invalid"}
client.post("/api/consultants", json={"name": "Api Formtest", "email": "api-form@example.invalid", "title": "QA", "country": "United States"})
api_c = next(c for c in models.get_candidates() if c["name"] == "Api Formtest")
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


check(api_c["visa_status"] == "" and api_c["target_rate"] == "", f"no invented defaults on create: {api_c['visa_status']!r} {api_c['target_rate']!r}")
check(api_c["experience_years"] is None and api_c["location"] == "", f"no invented experience/location: {api_c['experience_years']!r} {api_c['location']!r}")
client.post("/api/consultants", json={"name": "Apiindia Formtest", "email": "api-in@example.invalid", "title": "QA", "country": "India",
                                      "visa_status": "Open to US Relocation (H1B/L1 Sponsorship Needed)"})
api_in = next(c for c in models.get_candidates() if c["name"] == "Apiindia Formtest")
check(api_in["visa_status"] == "", f"India-based consultant gets no visa value: {api_in['visa_status']!r}")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
js_errors = []
shot = os.path.join(tmp_dir, "consultant_form.png")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.72.0" in page.content(), "cache-buster not bumped to 5.72.0")
        page.click("a.nav-item[data-tab=candidates]")
        page.click("#btn-add-consultant-tab")
        page.wait_for_selector("#modal-consultant", state="visible")
        check(page.input_value("#c-rate") == "" and page.input_value("#c-exp") == "", "rate / experience must not be pre-filled")

        # ---- India
        page.select_option("#c-country", "India")
        check(not page.is_visible("#c-visa-group"), "India: visa / relocation question hidden")
        check(page.text_content("#c-rate-label").strip() == "Expected Salary (CTC)" and "LPA" in page.get_attribute("#c-rate", "placeholder"),
              f"India rate label: {page.text_content('#c-rate-label')!r}")
        page.fill("#c-name", "India Formtest")
        page.fill("#c-email", "india-form@example.invalid")
        page.fill("#c-title", "AI Automation Engineer")
        page.fill("#c-skills", "Python, LangChain")
        page.fill("#c-rate", "₹12 LPA")
        page.fill("#c-location", "Hyderabad, India")
        page.click("#btn-save-consultant")
        page.wait_for_function("document.getElementById('modal-consultant').style.display === 'none'", timeout=10000)
        india = next((c for c in models.get_candidates() if c["name"] == "India Formtest"), None)
        check(india and india["experience_years"] is None, f"experience left empty stays empty: {india and india['experience_years']!r}")
        check(india and india["visa_status"] == "" and india["target_rate"] == "₹12 LPA" and india["country"] == "India",
              f"India consultant saved: {india and (india['visa_status'], india['target_rate'], india['country'])}")

        # ---- United States
        page.click("#btn-add-consultant-tab")
        page.wait_for_selector("#modal-consultant", state="visible")
        page.select_option("#c-country", "United States")
        check(page.is_visible("#c-visa-group") and page.text_content("#c-rate-label").strip() == "Target Rate ($/hr) *", "US: visa shown, $/hr rate")
        opts = page.eval_on_selector_all("#c-visa option", "els => els.filter(e => e.value).map(e => e.value)")
        for want in ("H1B", "H1B Transfer", "OPT", "STEM OPT", "CPT", "L2", "B1/B2", "US Citizen", "Green Card"):
            check(want in opts, f"US visa option {want!r} missing: {opts}")
        check("OPT/CPT" not in opts and "C2C Eligible" not in opts, f"combined/invented options must be gone: {opts}")
        page.fill("#c-name", "Us Formtest")
        page.fill("#c-email", "us-form@example.invalid")
        page.fill("#c-title", "Data Engineer")
        page.fill("#c-skills", "Spark, Python")
        page.fill("#c-rate", "$70/hr (C2C)")
        page.fill("#c-location", "Dallas, TX")
        page.select_option("#c-visa", "H1B")
        page.click("#btn-save-consultant")
        page.wait_for_function("document.getElementById('modal-consultant').style.display === 'none'", timeout=10000)
        us = next((c for c in models.get_candidates() if c["name"] == "Us Formtest"), None)
        check(us and us["visa_status"] == "H1B" and us["target_rate"] == "$70/hr (C2C)", f"US consultant saved: {us and (us['visa_status'], us['target_rate'])}")

        # ---- list shows real values, no invented ones
        page.reload()
        page.click("a.nav-item[data-tab=candidates]")
        page.wait_for_timeout(1500)
        pane = page.inner_text("#tab-consultants")
        check("India-based" in pane and "₹12 LPA" in pane, "India consultant shows India-based + salary")
        check("C2C Eligible" not in pane and "$90/hr" not in pane, "no invented C2C Eligible / $90/hr in the list")
        page.screenshot(path=shot)

        # ---- an older saved value is kept when editing
        page.evaluate(f"openConsultantModal(state.consultants.find(c => c.id === {old_id}))")
        page.wait_for_selector("#modal-consultant", state="visible")
        check(page.input_value("#c-visa") == "OPT/CPT" and "previously saved" in page.inner_text("#c-visa"),
              f"old value kept: {page.input_value('#c-visa')!r}")
        # ---- editing a consultant: a chosen resume file is uploaded, the summary is saved
        import docx as _docx  # noqa: E402
        doc = _docx.Document()
        doc.add_paragraph("US FORMTEST RESUME (fixture)")
        resume_path = os.path.join(tmp_dir, "us_formtest_resume.docx")
        doc.save(resume_path)
        page.keyboard.press("Escape")
        page.evaluate("closeConsultantModal()")
        page.evaluate(f"openConsultantModal(state.consultants.find(c => c.id === {us['id']}))")
        page.wait_for_selector("#modal-consultant", state="visible")
        page.fill("#c-summary", "Edited summary (test)")
        page.set_input_files("#c-resume-file", resume_path)
        page.click("#btn-save-consultant")
        page.wait_for_function("document.getElementById('modal-consultant').style.display === 'none'", timeout=15000)
        stored = models.get_resume_file(us["id"])
        edited = models.get_candidate_by_id(us["id"])
        check(stored and stored["filename"].endswith("us_formtest_resume.docx"), f"resume chosen while editing must be saved: {stored and stored['filename']}")
        check("US FORMTEST RESUME" in (edited.get("resume_text") or ""), "resume text extracted on edit upload")
        check(edited.get("resume_summary") == "Edited summary (test)", f"summary saved on edit: {edited.get('resume_summary')!r}")
        import models as _m  # noqa: E402
        check(_m.find_resume_file(edited) is not None, "a Gmail draft would now find the resume to attach")
        # the consultant list refreshes asynchronously after saving - wait for the saved summary to arrive
        page.wait_for_function(f"(state.consultants.find(c => c.id === {us['id']}) || {{}}).resume_summary === 'Edited summary (test)'", timeout=10000)
        page.evaluate(f"openConsultantModal(state.consultants.find(c => c.id === {us['id']}))")
        check(page.input_value("#c-summary") == "Edited summary (test)", "edit form shows the saved summary")
        browser.close()
finally:
    server.shutdown()
    import glob  # noqa: E402
    for leftover in glob.glob(os.path.join(models.RESUMES_DIR, "*us_formtest_resume.docx")):
        os.remove(leftover)   # the upload route also writes the file into data/resumes

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshot:", shot)
    sys.exit(1)
print(f"PASS: consultant form in Edge - India: no visa + salary; US: separate H1B/OPT/CPT/L2/B1-B2; no invented defaults; old values kept. Screenshot: {shot}")
