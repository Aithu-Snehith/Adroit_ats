"""Browser test (Playwright + Microsoft Edge): the Vendors tab.

A recruiter uploads an Excel file -> preview (new / duplicate / problem) -> imports the new rows ->
sees them in the table; changes a status, edits a contact, adds one by hand, searches. Another
recruiter sees none of it; the admin sees both lists with a Recruiter column + filter. Also checks
the layout at 1280px width. No network. People / companies are labelled test fixtures.
Run: python test_vendors_browser.py
"""
import os
import socket
import sys
import tempfile
import threading

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendors_ui.db")
os.environ["DATABASE_URL"] = ""
os.environ["APIFY_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}

USERS = {"admin": ("Vend Admin", "vui-admin@example.invalid", "VendUi-1", "Admin"),
         "asha": ("Asha Vendui", "vui-asha@example.invalid", "VendUi-2", "Recruiter"),
         "ravi": ("Ravi Vendui", "vui-ravi@example.invalid", "VendUi-3", "Recruiter")}
for name, email, pw, role in USERS.values():
    models.create_user(name, email, pw, role=role)

xlsx = os.path.join(tmp_dir, "asha_vendors.xlsx")
wb = Workbook()
ws = wb.active
ws.append(["Company", "Contact Name", "Email", "Phone", "Notes"])
ws.append(["Vendor One Test Inc", "Kiran Testone", "kiran@vendor-one.example", "+1 555 0100", "Works for AT&T clients"])
ws.append(["Vendor One Test Inc", "Lata Testone", "lata@vendor-one.example", ""])
ws.append(["Vendor Two Test", "Mohan Testtwo", "mohan@vendor-two.example", ""])
ws.append(["Vendor One Test Inc", "Kiran Dup", "kiran@vendor-one.example", ""])
ws.append(["", "Om Testfour", "om.vendor@gmail.com", ""])
wb.save(xlsx)

with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
server = make_server("127.0.0.1", port, app_module.app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
failures, js_errors = [], []
shots = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


ROWS = "document.querySelectorAll('#vendors-body tr[data-id]').length"


def open_vendors(browser, who, width=1440):
    page = browser.new_page(viewport={"width": width, "height": 950})
    page.on("pageerror", lambda e: js_errors.append(f"{who}: {e}"))
    page.goto(f"http://127.0.0.1:{port}/login")
    page.fill("input[name=email]", USERS[who][1])
    page.fill("input[name=password]", USERS[who][2])
    page.click("button[type=submit]")
    page.wait_for_url("**/dashboard**")
    if width >= 1000:
        page.click("a.nav-item[data-tab=vendors]")
    else:
        page.evaluate("switchTab('vendors')")
    page.wait_for_function("document.getElementById('vendors-count').textContent !== 'Loading...'", timeout=10000)
    return page


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---- Asha uploads
        page = open_vendors(browser, "asha")
        check("app.js?v=5.88.0" in page.content(), "cache-buster not bumped to 5.88.0")
        check(page.is_visible("#tab-vendors") and "vendor contacts yet" in page.inner_text("#vendors-body"), "empty state")
        check(not page.is_visible("#vendors-owner"), "recruiter has no Recruiter filter")
        check(page.get_attribute("#vendors-template", "href") == "/api/vendors/template", "template link")
        page.set_input_files("#vendors-file", xlsx)
        page.wait_for_selector("#vendors-preview", state="visible", timeout=10000)
        counts = page.inner_text("#vp-counts")
        check("3 new" in counts and "1 already" in counts and "1 with a problem" in counts, f"preview counts: {counts}")
        check(page.inner_text("#vp-import") == "Import 3 new contacts", f"import button: {page.inner_text('#vp-import')}")
        check(page.locator("#vp-table tbody tr").count() == 5, "preview lists every row")
        check("Works for AT&T clients" in page.locator("#vp-table tbody tr", has_text="kiran@vendor-one.example").first.inner_text(), "notes shown in the preview")
        shots.append(os.path.join(tmp_dir, "vendors_preview.png"))
        page.screenshot(path=shots[-1], full_page=True)
        page.click("#vp-import")
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        check(not page.is_visible("#vendors-preview"), "preview closes after import")
        kiran = page.locator("#vendors-body tr", has_text="kiran@vendor-one.example")
        check(kiran.locator(".vendor-notes").inner_text() == "Works for AT&T clients", "notes shown in the Vendors table")
        check("Notes" in page.text_content("#vendors-table thead"), "Notes column header")
        check(page.inner_text("#vendors-count") == "USA list: 3 contacts at 2 companies", f"count: {page.inner_text('#vendors-count')}")
        check(not page.is_visible("#vendors-table th.vendors-owner-col"), "no Recruiter column for a recruiter")
        color = page.eval_on_selector("#vendors-body tr[data-id] td", "el => getComputedStyle(el).color")
        check(color != "rgb(255, 255, 255)", f"company text visible: {color}")

        # status change persists
        lata_row = page.locator("#vendors-body tr", has_text="lata@vendor-one.example")
        lata_row.locator("select.vendor-status").select_option("bounced")
        page.wait_for_timeout(500)
        page.reload()
        page.click("a.nav-item[data-tab=vendors]")
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        check(page.locator("#vendors-body tr", has_text="lata@vendor-one.example").locator("select").input_value() == "bounced", "status saved")

        # edit
        page.locator("#vendors-body tr", has_text="mohan@vendor-two.example").locator("button", has_text="Edit").click()
        check(page.is_disabled("#vf-email") and page.input_value("#vf-company") == "Vendor Two Test", "edit form prefilled, email locked")
        page.fill("#vf-phone", "+91 98765 43210")
        page.click("#vf-save")
        page.wait_for_function("document.querySelector('#vendors-body').innerText.includes('+91 98765 43210')", timeout=10000)

        # add by hand + search
        page.click("#vendors-add-btn")
        check(not page.is_disabled("#vf-email") and page.input_value("#vf-email") == "", "add form empty")
        page.fill("#vf-company", "Vendor Three Test")
        page.fill("#vf-email", "sita@vendor-three.example")
        page.click("#vf-save")
        page.wait_for_function(f"{ROWS} === 4", timeout=10000)
        page.fill("#vendors-search", "vendor-three")
        page.wait_for_function(f"{ROWS} === 1", timeout=10000)
        page.fill("#vendors-search", "")
        page.wait_for_function(f"{ROWS} === 4", timeout=10000)

        # delete
        page.once("dialog", lambda d: d.accept())
        page.locator("#vendors-body tr", has_text="sita@vendor-three.example").locator("button", has_text="Delete").click()
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        page.close()

        # ---- Ravi sees nothing of Asha's
        page = open_vendors(browser, "ravi")
        check(page.evaluate(ROWS) == 0 and "vendor contacts yet" in page.inner_text("#vendors-body"), "Ravi sees none of Asha's contacts")
        page.close()

        # ---- admin sees all, with the Recruiter column + filter
        page = open_vendors(browser, "admin")
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        check(page.is_visible("#vendors-owner") and page.is_visible("#vendors-table th.vendors-owner-col"), "admin: filter + Recruiter column")
        check("Asha Vendui" in page.inner_text("#vendors-body"), "admin sees the owner name")
        check("every recruiter" in page.inner_text("#vendors-subtitle"), "admin subtitle")
        opts = page.eval_on_selector_all("#vendors-owner option", "els => els.map(o => o.textContent)")
        check(opts == ["All recruiters", "Asha Vendui"], f"owner options: {opts}")
        page.close()

        # ---- small laptop: the table scrolls inside its box, never the page
        # (phone width isn't checked: the whole dashboard - sidebar + header - isn't built for phones yet)
        page = open_vendors(browser, "asha", width=1280)
        page.wait_for_function(f"{ROWS} === 3", timeout=10000)
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(overflow <= 1, f"1280px wide: page scrolls sideways by {overflow}px")
        shots.append(os.path.join(tmp_dir, "vendors_1280.png"))
        page.screenshot(path=shots[-1], full_page=True)
        browser.close()
finally:
    server.shutdown()

check(not js_errors, f"JavaScript errors: {js_errors}")
if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    print("screenshots:", shots)
    sys.exit(1)
print(f"PASS: Vendors tab in Edge - upload preview + import, status/edit/add/search/delete, private per recruiter, admin sees all, 1280px width. Screenshots: {shots}")
