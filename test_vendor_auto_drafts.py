"""Known vendors in Jobs: the search tags each job with the recruiter's own vendor contacts; 1-Click
Draft BCCs them automatically; Browse Jobs' automatic drafts - only known-vendor jobs, only ones
that fit the consultant's experience, never the same job + consultant twice, at most 10 per
consultant per day, To = the job's recruiter (else the first vendor contact, without overwriting
the job), stops when Gmail isn't connected; other recruiters can't use it.
Gmail is never contacted (imaplib stubbed); scrapers stubbed. Throwaway SQLite DB.
People / companies are labelled test fixtures.
Run: python test_vendor_auto_drafts.py
"""
import email
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_vendor_auto.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import gmail_multi_manager as gm  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import us_job_scrapers  # noqa: E402
import vendors  # noqa: E402

us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: {"jobs": [], "count": 0}
failures, captured = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class FakeIMAP:
    def __init__(self, host, port=None):
        pass

    def login(self, user, pw):
        pass

    def append(self, folder, flags, when, raw):
        captured.append(email.message_from_bytes(raw))

    def logout(self):
        pass


gm.imaplib.IMAP4_SSL = FakeIMAP


def uid(u):
    return u["id"] if isinstance(u, dict) else u


ASHA = {"id": uid(models.create_user("Asha Autotest", "auto-asha@example.invalid", "Auto-1", role="Recruiter")), "name": "Asha Autotest", "role": "Recruiter"}
RAVI = {"id": uid(models.create_user("Ravi Autotest", "auto-ravi@example.invalid", "Auto-2", role="Recruiter")), "name": "Ravi Autotest", "role": "Recruiter"}
cid = models.create_candidate("Kumar Autotest", "kumar-auto@example.invalid", title="Java Developer", experience_years=4,
                              gmail_account="kumar-auto@example.invalid", assigned_user_id=ASHA["id"])
models.update_candidate(cid, gmail_app_password="test-app-password-not-real")
nogmail = models.create_candidate("NoGmail Autotest", "nogmail-auto@example.invalid", title="Java Developer", experience_years=4,
                                  assigned_user_id=ASHA["id"])

conn = models.get_db_connection()
vendors.import_rows(conn, ASHA["id"], [
    {"company": "Vendor One Test Inc", "name": "Kiran", "email": "kiran@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "Lata", "email": "lata@vendor-one.example"},
    {"company": "Vendor One Test Inc", "name": "Mohan", "email": "mohan@vendor-one.example"},
    {"company": "Vendor Two Test", "name": "Nina", "email": "nina.v2@gmail.com"},
    {"company": "Vendor Two Test", "name": "Om", "email": "om.v2@gmail.com"},
])
conn.close()


def job(title, desc, company="Test Co", rec=""):
    j = models.save_or_update_scraped_job({"title": title, "company": company, "source": "Dice", "job_type": "Contract",
                                          "country": "United States", "recruiter_email": rec,
                                          "url": f"https://www.dice.com/job-detail/auto-{abs(hash(title))}", "description": desc})
    return j["id"] if isinstance(j, dict) else j


J1 = job("Java Developer Auto One", "Experience: 3-6 Yrs.", company="Vendor One Test Inc", rec="poster@vendor-one.example")
J2 = job("Java Developer Auto Two", "Java role", company="Vendor Two Test, LLC")
J3 = job("Java Developer Auto Senior", "Need 8+ years of experience.", company="Vendor One Test Inc", rec="poster@vendor-one.example")
J4 = job("Java Developer Auto Unknown", "Java role", company="Unknown Test Co", rec="x@unknown.example")
EXTRA = [job(f"Java Developer Auto Extra {i:02d}", "Java role, 2+ years", company="Vendor One Test Inc") for i in range(12)]

client = app_module.app.test_client()


def login(u):
    with client.session_transaction() as s:
        s["user"] = u


# ---- search tags known vendors (own list only)
login(ASHA)
jobs = client.post("/api/jobs/search", json={"query": "java developer auto", "country": "United States", "is_24h_only": True}).get_json()["jobs"]
tag = {j["id"]: j.get("vendor") for j in jobs}
check(tag.get(J1) == {"company": "Vendor One Test Inc", "count": 3}, f"J1 tagged by domain: {tag.get(J1)}")
check(tag.get(J2) == {"company": "Vendor Two Test", "count": 2}, f"J2 tagged by company name: {tag.get(J2)}")
check(tag.get(J4) is None, "unknown company not tagged")
login(RAVI)
jobs = client.post("/api/jobs/search", json={"query": "java developer auto", "country": "United States", "is_24h_only": True}).get_json()["jobs"]
check(all(not j.get("vendor") for j in jobs), "Ravi (no vendors) sees no tags")

# ---- 1-Click Draft BCCs automatically; vendor_bcc=false turns it off
login(ASHA)
r = client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": J1}).get_json()
check(r.get("success") and sorted(r.get("bcc", [])) == ["kiran@vendor-one.example", "lata@vendor-one.example", "mohan@vendor-one.example"], f"1-click bcc: {r}")
check(captured[-1]["To"] == "poster@vendor-one.example" and "kiran@vendor-one.example" in captured[-1]["Bcc"], "1-click headers")
r = client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": J4}).get_json()
check(r.get("success") and r.get("bcc") == [] and captured[-1]["Bcc"] is None, "no BCC for an unknown company")
r = client.post("/api/outreach/create-draft", json={"candidate_id": cid, "job_id": J3, "vendor_bcc": False}).get_json()
check(r.get("success") and captured[-1]["Bcc"] is None, "vendor_bcc=false -> no BCC")
# J3 now has an application, but the auto-draft must skip it on experience anyway; reset that draft
conn = models.get_db_connection()
conn.cursor().execute("DELETE FROM applications WHERE job_id = ?", (J3,))
conn.commit()
conn.close()

# ---- automatic drafts
captured.clear()
r = client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": cid, "job_ids": [J1, J2, J3, J4] + EXTRA}).get_json()
made = [c["job_id"] for c in r["created"]]
check(J1 not in made and r["skipped"]["already_drafted"] == 1, f"J1 already drafted by 1-click -> skipped: {r['skipped']}")
check(J3 not in made and r["skipped"]["experience"] == 1, "8+ years job skipped for a 4-year consultant")
check(J4 not in made and r["skipped"]["not_vendor"] == 1, "unknown company skipped")
check(made[0] == J2 and len(made) == 10 and r["skipped"]["daily_limit"] == 3, f"10 per day: {len(made)} made, skipped {r['skipped']}")
check(r["remaining_today"] == 0, "nothing left today")
j2 = next(c for c in r["created"] if c["job_id"] == J2)
check(j2["to"] == "nina.v2@gmail.com" and j2["bcc"] == 1, f"J2: To = first vendor contact, BCC = the other: {j2}")
check(captured[0]["To"] == "nina.v2@gmail.com" and captured[0]["Bcc"] == "om.v2@gmail.com", "J2 draft headers")
check(not (models.get_job_by_id(J2).get("recruiter_email") or ""), "the job's recruiter email is not overwritten with the vendor contact")
extra = next(c for c in r["created"] if c["job_id"] == EXTRA[0])
check(extra["to"] == "kiran@vendor-one.example" and extra["bcc"] == 2, f"extra: To first contact, BCC other 2: {extra}")
check(len(captured) == 10, f"10 drafts appended: {len(captured)}")

# again today: nothing new (limit + no repeats)
captured.clear()
r = client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": cid, "job_ids": [J2] + EXTRA}).get_json()
check(r["created"] == [] and r["skipped"]["already_drafted"] == 10 and r["skipped"]["daily_limit"] == 3 and not captured, f"second run: {r['skipped']}")

# tomorrow (move today's log back a day): the 3 left over are drafted, repeats still skipped
conn = models.get_db_connection()
conn.cursor().execute("UPDATE vendor_auto_drafts SET created_at = ?",   # yesterday (works on SQLite and Postgres)
                      ((__import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),))
conn.commit()
conn.close()
r = client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": cid, "job_ids": [J2] + EXTRA}).get_json()
check(len(r["created"]) == 3 and r["skipped"]["already_drafted"] == 10 and r["remaining_today"] == 7, f"next day: {len(r['created'])} made, {r['skipped']}")

# Gmail not connected: one error, stops, nothing logged
r = client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": nogmail, "job_ids": EXTRA}).get_json()
check(r["created"] == [] and "Gmail not connected" in r["error"], f"no Gmail: {r}")
conn = models.get_db_connection()
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM vendor_auto_drafts WHERE candidate_id = ?", (nogmail,))
check(cur.fetchone()[0] == 0, "nothing logged when the draft failed")
conn.close()

# another recruiter can't run it for Asha's consultant; login required
login(RAVI)
check(client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": cid, "job_ids": EXTRA}).status_code == 404, "Ravi -> 404")
with client.session_transaction() as s:
    s.clear()
check(client.post("/api/outreach/auto-vendor-drafts", json={"candidate_id": cid, "job_ids": EXTRA}).status_code == 401, "login required")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: known vendors in Jobs - search tags, 1-click auto BCC, automatic drafts (vendor-only, experience, no repeats, 10/day, To fallback, Gmail check, privacy).")
