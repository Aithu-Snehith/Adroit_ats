"""USA / India teams in one app (regions.py).

Existing users get a team from their consultants' countries; a recruiter sees and acts on only their
team's consultants (list, dashboard, drafts, tracker); the Jobs feed is the team's market; a
recruiter can't create / move a consultant into the other team's country (admins can); admins
switch USA / India / Both and the Vendors view follows; admins move recruiters between teams.
No network (scrapers / Gmail stubbed). Throwaway SQLite DB. Labelled test fixtures.
Run: python test_regions.py
"""
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_regions.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
import app as app_module  # noqa: E402
import india_job_scrapers  # noqa: E402
import models  # noqa: E402
import regions  # noqa: E402
import us_job_scrapers  # noqa: E402
import vendors  # noqa: E402

scraped = []
us_job_scrapers.run_multi_source_us_scrape = lambda *a, **k: scraped.append("US") or {"jobs": [], "count": 0}
india_job_scrapers.run_multi_source_india_scrape = lambda *a, **k: scraped.append("India") or {"jobs": [], "count": 0}
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
us_rec = {"id": uid(models.create_user("Usa Regiontest", "usa-reg@example.invalid", "Reg-1", role="Recruiter")), "name": "Usa Regiontest", "role": "Recruiter"}
in_rec = {"id": uid(models.create_user("India Regiontest", "india-reg@example.invalid", "Reg-2", role="Recruiter")), "name": "India Regiontest", "role": "Recruiter"}
admin = {"id": uid(models.create_user("Admin Regiontest", "admin-reg@example.invalid", "Reg-3", role="Admin")), "name": "Admin Regiontest", "role": "Admin"}
c_us = models.create_candidate("Koushik Usateam", "k-us@example.invalid", title="Java Developer", assigned_user_id=us_rec["id"])
models.update_candidate(c_us, country="United States")
c_in = models.create_candidate("Visakh Indiateam", "v-in@example.invalid", title="DevOps", assigned_user_id=in_rec["id"])
models.update_candidate(c_in, country="India")

# ---- backfill: existing users get a team from their consultants
conn = models.get_db_connection()
conn.cursor().execute("UPDATE users SET region = NULL")
conn.commit()
regions.backfill(conn)
check(regions.user_region(conn, in_rec["id"]) == "India" and regions.user_region(conn, us_rec["id"]) == "USA", "backfill by consultants' countries")
conn.close()

client = app_module.app.test_client()


def login(u):
    with client.session_transaction() as s:
        s["user"] = u


def names(resp):
    return sorted(c["name"] for c in resp.get_json())


# ---- each recruiter sees only their team
login(in_rec)
check(names(client.get("/api/consultants")) == ["Visakh Indiateam"], "India recruiter sees India consultants")
check("Koushik Usateam" not in client.get("/dashboard").get_data(as_text=True), "dashboard shows only the team")
r = client.post("/api/jobs/search", json={"query": "devops", "country": "United States"}).get_json()
check(all((j.get("country") or "India") == "India" for j in r["jobs"]) and scraped[-1:] == ["India"],
      f"India recruiter gets India jobs even if US is asked (source used: {scraped[-1:]})")
r = client.post("/api/consultants", json={"name": "Wrong Teamtest", "email": "wrong@example.invalid", "country": "United States"})
check(r.status_code == 400 and "USA team" in r.get_json()["error"], f"India recruiter can't add a US consultant: {r.get_json()}")
r = client.post("/api/consultants", json={"name": "Right Teamtest", "email": "right@example.invalid", "country": "India"})
check(r.status_code in (200, 201), f"India recruiter adds an India consultant: {r.status_code} {r.get_json()}")
r = client.put(f"/api/consultants/{c_in}", json={"country": "United States"})
check(r.status_code == 400, "a recruiter can't move a consultant to the other team")
login(us_rec)
check(names(client.get("/api/consultants")) == ["Koushik Usateam"], "USA recruiter sees USA consultants")
check(client.get(f"/api/consultants/{c_in}").status_code == 404, "USA recruiter can't open an India consultant")

# ---- admin: Both by default, switcher narrows
login(admin)
all_names = names(client.get("/api/consultants"))
check("Koushik Usateam" in all_names and "Visakh Indiateam" in all_names, f"admin sees both: {all_names}")
client.post("/api/view-region", json={"region": "India"})
check("Koushik Usateam" not in names(client.get("/api/consultants")), "admin viewing India")
r = client.post("/api/jobs/search", json={"query": "devops", "country": "United States"}).get_json()
check(all((j.get("country") or "India") == "India" for j in r["jobs"]) and scraped[-1:] == ["India"], "admin India view -> India jobs")
client.post("/api/view-region", json={"region": ""})
check(len(names(client.get("/api/consultants"))) >= 3, "back to Both")

# ---- admins move recruiters; admin vendors view follows the team
r = client.post(f"/api/admin/recruiters/{us_rec['id']}/region", json={"region": "India"})
check(r.status_code == 200 and r.get_json()["region"] == "India", "admin moves a recruiter")
client.post(f"/api/admin/recruiters/{us_rec['id']}/region", json={"region": "USA"})
check(client.post(f"/api/admin/recruiters/{us_rec['id']}/region", json={"region": "Mars"}).status_code == 400, "only USA / India")
lst = client.get("/api/admin/recruiters").get_json()
check({u["email"]: u["region"] for u in lst}.get("india-reg@example.invalid") == "India", "roster shows the team")
r = client.post("/api/admin/recruiters", json={"name": "New Indiatest", "email": "new-in@example.invalid", "password": "New-1", "region": "India"})
conn = models.get_db_connection()
check(r.status_code == 200 and regions.user_region(conn, uid(r.get_json()["user"])) == "India", "new recruiter created on the India team")
vendors.import_rows(conn, us_rec["id"], [{"company": "Us Vendor Test", "email": "a@us-vendor.example"}])
vendors.import_rows(conn, in_rec["id"], [{"company": "In Vendor Test", "email": "b@in-vendor.example"}], region="India")
conn.close()
client.post("/api/view-region", json={"region": "India"})
check([c["email"] for c in client.get("/api/vendors").get_json()["contacts"]] == ["b@in-vendor.example"], "admin India view: India vendors only")
client.post("/api/view-region", json={"region": ""})
both = client.get("/api/vendors").get_json()
check(both["region"] == "USA" and both["region_counts"] == {"USA": 1, "India": 1}, f"Both: opens on the USA list, counts for both tabs: {both}")
login(in_rec)
check(client.post("/api/view-region", json={"region": "USA"}).status_code == 403, "recruiters can't switch team")

# ---- drafts and tracker respect the team
login(us_rec)
check(client.post("/api/outreach/create-draft", json={"candidate_id": c_in, "job_id": 1}).status_code == 404, "can't draft for the other team's consultant")
check(client.get(f"/api/applications?candidate_id={c_in}").status_code == 404, "can't open the other team's tracker")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: USA / India teams - backfill, team-only consultants / dashboard / drafts / tracker, team jobs feed, add/move guard, admin switcher, vendors view, recruiter team changes.")
