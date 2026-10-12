"""Browser test (Playwright + Microsoft Edge): finding and adding colleges in Sourcing.

Typing "srinidhi college of engineering" in Filter A suggests Sreenidhi Institute of Science and
Technology (look-alike spelling, generic words ignored); a name not on the list says how to add it;
an admin adds a college in Admin & Settings and it is then in the Filter A suggestions.
No network. Labelled test fixtures.
Run: python test_add_college_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_add_college_ui.db")
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

EMAIL, PASSWORD = "colui@example.invalid", "ColUi-Test-1"
models.create_user("College UI Admin", EMAIL, PASSWORD, role="Admin")

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shot = os.path.join(tmp_dir, "add_college.png")


def check(cond, msg):
    if not cond:
        failures.append(msg)


def suggestions(page, text):
    page.fill("#edu-inst-input", "")
    page.type("#edu-inst-input", text)
    page.wait_for_function("document.getElementById('edu-inst-list').style.display !== 'none'", timeout=5000)
    page.wait_for_timeout(200)
    return page.inner_text("#edu-inst-list")


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
        check("app.js?v=5.80.0" in page.content(), "cache-buster not bumped to 5.80.0")
        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")

        text = suggestions(page, "srinidhi college of engineering")
        check("Sreenidhi Institute of Science and Technology" in text, f"look-alike spelling found: {text!r}")
        text = suggestions(page, "srinidhi")
        check("Sreenidhi Institute of Science and Technology" in text, f"'srinidhi' alone: {text!r}")
        text = suggestions(page, "osmania")
        check("Osmania University" in text and "Sreenidhi" not in text, "normal search unchanged")
        text = suggestions(page, "Example Hill College Testgiri")
        check("No college in the list matches" in text and "Add a college / university" in text, f"how to add: {text!r}")

        # admin adds it
        page.click("a.nav-item[data-tab=settings]")
        page.wait_for_selector("#add-college-panel", state="visible")
        page.fill("#ac-name", "Example Hill College of Engineering Testgiri")
        page.select_option("#ac-country", "India")
        page.fill("#ac-city", "Testgiri")
        page.fill("#ac-aliases", "EHCE")
        page.click("#ac-add")
        page.wait_for_function("document.getElementById('ac-status').innerText.includes('Added')", timeout=10000)
        check("Filter A list" in page.inner_text("#ac-status"), f"status: {page.inner_text('#ac-status')}")
        check(page.input_value("#ac-name") == "", "form cleared after adding")
        page.screenshot(path=shot)
        page.click("#ac-add")   # empty form -> a clear error, nothing added
        page.wait_for_function("document.getElementById('ac-status').innerText.includes('full name')", timeout=5000)

        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        text = suggestions(page, "EHCE")
        check("Example Hill College of Engineering Testgiri" in text, f"new college in Filter A: {text!r}")
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
print(f"PASS: colleges in Edge - 'srinidhi college of engineering' finds Sreenidhi, how-to-add hint, admin adds a college, it shows in Filter A. Screenshot: {shot}")
