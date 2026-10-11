"""Browser test (Playwright + Microsoft Edge): Sourcing tracker card.

Click a candidate row -> the card slides in; fill email, phone, visa, location, relocate, rate,
availability, follow-up; save; the row shows the contact icons, visa, owner and "Today"; change the
status (the recruiter becomes the owner); post a comment (HTML shown as text) and delete it; Add to
Bench; Esc closes; "My candidates" filter; the card fits a phone-width screen. No network beyond the
local app. People are labelled test fixtures.
Run: python test_sourcing_tracker_browser.py
"""
import os
import socket
import sys
import tempfile
import threading
from datetime import date

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_card_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import linkedin_ingest as li  # noqa: E402
import models  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

EMAIL, PASSWORD = "card-ui@example.invalid", "CardUi-Test-1"
models.create_user("Pramod Cardui", EMAIL, PASSWORD, role="Recruiter")


def item(slug, first):
    return {"linkedinUrl": f"https://www.linkedin.com/in/{slug}.example.invalid", "firstName": first, "lastName": "Uicard",
            "headline": "Data Engineer", "location": {"linkedinText": "Dallas, Texas"},
            "currentPosition": [{"position": "Data Engineer", "companyName": "Test Co"}],
            "education": [{"schoolName": "CBIT", "degree": "B.Tech", "endDate": {"year": 2020}},
                          {"schoolName": "UNT", "degree": "MS", "endDate": {"year": 2023}}]}


li.ingest_profiles(li.ApifyProfileProvider().to_profiles([item("card-ui-1", "Ravi"), item("card-ui-2", "Sita")]), "apify", None)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


OPEN = "document.getElementById('trk-drawer').style.transform === 'translateX(0px)'"
shot = os.path.join(tmp_dir, "card.png")
today = date.today().isoformat()
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 950})
        page.on("pageerror", lambda e: js_errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"http://127.0.0.1:{port}/login")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url("**/dashboard**")
        check("app.js?v=5.72.0" in page.content(), "cache-buster not bumped to 5.72.0")

        page.click("a.nav-item[data-tab=sourcing]")
        page.click(".edu-tab[data-filter=A]")
        page.fill("#edu-inst-input", "CBIT")
        page.wait_for_selector(".edu-inst-option")
        page.click(".edu-inst-option")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 2")
        headers = page.eval_on_selector_all("#edu-table thead th", "els => els.map(e => e.textContent.trim())")
        check(headers[-5:] == ["Status", "Contact", "Visa", "Owner", "Follow-up"], f"headers: {headers}")

        ravi = page.locator("#edu-results tr[data-pid]", has_text="Ravi Uicard")
        pid = ravi.get_attribute("data-pid")
        ravi.locator("td").nth(2).click()   # clicking the row (Location cell) opens the card
        page.wait_for_function(OPEN)
        check(page.input_value("#trk-location") == "Dallas, Texas", "current location starts from LinkedIn")
        check(page.input_value("#trk-email") == "" and page.input_value("#trk-visa") == "", "contact fields start empty")

        page.fill("#trk-email", "ravi@example.invalid")
        page.fill("#trk-phone", "+1 469 555 0100")
        page.select_option("#trk-visa", "H1B")
        page.select_option("#trk-relocate", "Yes")
        page.fill("#trk-rate", "$65/hr C2C")
        page.select_option("#trk-availability", "2 weeks")
        page.fill("#trk-followup", today)
        page.click("#trk-save")
        page.wait_for_function("document.getElementById('trk-activity').innerText.includes('Updated email, phone')")
        page.wait_for_function(f"document.querySelector('#edu-results tr[data-pid=\"{pid}\"]').innerText.includes('H1B')")
        row = page.inner_text(f"#edu-results tr[data-pid='{pid}']")
        check("Today" in row, f"follow-up today shown in the row: {row!r}")
        check(page.input_value("#trk-phone") == "+1 469 555 0100", f"phone shown readable: {page.input_value('#trk-phone')!r}")
        zs = page.evaluate("[parseInt(getComputedStyle(document.getElementById('toast-container')).zIndex), parseInt(document.getElementById('trk-drawer').style.zIndex)]")
        check(zs[0] > zs[1], f"pop-up messages must show above the card: {zs}")
        opac = page.eval_on_selector_all(f"#edu-results tr[data-pid='{pid}'] td:nth-child(7) span", "els => els.map(e => e.style.opacity)")
        check(opac == ["1", "1"], f"contact icons lit: {opac}")

        # phone without a country code is refused, with a message saying why
        page.fill("#trk-phone", "4695550100")
        page.click("#trk-save")
        page.wait_for_function("document.body.innerText.includes('country code')", timeout=5000)
        page.fill("#trk-phone", "+1 469 555 0100")

        # status from the card -> owner = me
        page.select_option("#trk-card-status", "Contacted")
        page.wait_for_function("document.getElementById('trk-activity').innerText.includes('Owner: Pramod Cardui')")
        page.wait_for_function(f"document.querySelector('#edu-results tr[data-pid=\"{pid}\"]').innerText.includes('Pramod Cardui')")

        # comment shown as text, then deleted
        page.fill("#trk-comment", "Called - interested <b>x</b>")
        page.click("#trk-post")
        page.wait_for_function("document.getElementById('trk-activity').innerText.includes('Called - interested')")
        check("&lt;b&gt;x&lt;/b&gt;" in page.inner_html("#trk-activity"), "comment HTML is shown as text")
        page.wait_for_function(f"document.querySelector('#edu-results tr[data-pid=\"{pid}\"] .trk-card-open').innerText.includes('💬 1')")
        page.screenshot(path=shot)
        page.click("#trk-activity .trk-del")
        page.wait_for_function("!document.getElementById('trk-activity').innerText.includes('Called - interested')")

        # Add to Bench
        page.click("#trk-bench")
        page.wait_for_function("document.getElementById('trk-drawer').innerText.includes('On the bench')")
        bench = [c for c in models.get_candidates() if c["name"] == "Ravi Uicard"]
        check(len(bench) == 1 and bench[0]["email"] == "ravi@example.invalid" and bench[0]["visa_status"] == "H1B", f"bench: {bench}")

        # Esc closes; "My candidates" shows only Ravi
        page.keyboard.press("Escape")
        page.wait_for_function("document.getElementById('trk-drawer').style.transform.includes('105%')")
        page.check("#edu-f-mine")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 1")
        check("Ravi Uicard" in page.inner_text("#edu-results"), "My candidates filter")
        page.uncheck("#edu-f-mine")
        page.wait_for_function("document.querySelectorAll('#edu-results tr[data-pid]').length === 2")

        # the LinkedIn link opens LinkedIn, not the card
        sita = page.locator("#edu-results tr[data-pid]", has_text="Sita Uicard")
        page.context.route("**/linkedin.com/**", lambda route: route.abort())
        with page.context.expect_page() as newp:
            sita.locator("a").first.click()
        newp.value.close()
        check(page.evaluate("document.getElementById('trk-drawer').style.transform.includes('105%')"), "the LinkedIn link must not open the card")

        # phone-width screen: the card fits
        # (the app's sidebar isn't built for phones and covers the table, so open the card directly)
        page.set_viewport_size({"width": 390, "height": 800})
        page.evaluate(f"trkOpen({sita.get_attribute('data-pid')})")
        page.wait_for_function(OPEN)
        page.wait_for_timeout(300)
        box = page.evaluate("(() => { const r = document.getElementById('trk-drawer').getBoundingClientRect(); return [r.left, r.right, window.innerWidth]; })()")
        check(box[0] >= -1 and box[1] <= box[2] + 1, f"card fits a 390px screen: {box}")
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
print(f"PASS: tracker card in Edge - fields, row icons/visa/owner/Today, owner rule, comments, Add to Bench, Esc, My candidates, phone width. Screenshot: {shot}")
