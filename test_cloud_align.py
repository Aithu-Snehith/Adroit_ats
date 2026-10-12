"""Cloud alignment (cloud_align.py + optimizer + /api/jd/cloud).

Detects the JD's primary cloud (required/primary lines outweigh "nice to have"); Java lambdas are not
AWS; checks summary / skills / most recent project of the optimized resume; offers equivalent
services only as talking points; the optimizer tells the AI the target cloud AND forbids relabelling
past work; /api/jd/cloud compares the JD with the consultant's profile (own consultants only).
Gemini is never called (stub). Throwaway SQLite DB. Labelled test fixtures.
Run: python test_cloud_align.py
"""
import json
import os
import sys
import tempfile

tmp_dir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp_dir, "test_cloud.db")
os.environ["DATABASE_URL"] = ""
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

config.DB_PATH = os.environ["DB_PATH"]
config.DATABASE_URL = ""
config.GEMINI_API_KEY = "test-key-not-real"
import app as app_module  # noqa: E402
import cloud_align as ca  # noqa: E402
import models  # noqa: E402
import resume_bot as rb  # noqa: E402

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


AZURE_JD = """Senior Data Engineer (Azure)
Must have 5+ years with Azure Data Factory, Synapse and Databricks.
Hands-on AKS and Azure Functions required.
AWS or GCP experience is a plus."""

# ---- detection
j = ca.jd_cloud(AZURE_JD)
check(j["primary"] == "Azure" and set(j["mentioned"]) == {"AWS", "Azure", "GCP"}, f"Azure primary, others mentioned: {j}")
check(ca.jd_cloud("Strong AWS (EC2, S3, Lambda functions) required. Azure nice to have.")["primary"] == "AWS", "required beats nice-to-have")
check(ca.jd_cloud("BigQuery, Dataflow and GKE are mandatory.")["primary"] == "GCP", "GCP via services")
check(ca.jd_cloud("Java 17 developer: streams, lambda expressions, Spring Boot.")["primary"] is None, "Java lambdas are not AWS")
# "Azure, AWS, or GCP": alternatives -> any cloud is fine, no primary (an AWS candidate is a match)
ANY_JD = ("Design CI/CD pipelines using Jenkins, Azure DevOps, GitHub Actions, or GitLab CI. Manage and support cloud environments "
          "on Azure, AWS, or GCP. Experience with Docker, Kubernetes (AKS/EKS/OpenShift). Expertise in Terraform, ARM Templates, "
          "CloudFormation. Experience with AWS, Azure, or GCP cloud platforms.")
j = ca.jd_cloud(ANY_JD)
check(j["any_cloud"] and j["primary"] is None, f"alternatives = any cloud: {j}")
check(not ca.jd_cloud(AZURE_JD)["any_cloud"], "'AWS or GCP is a plus' is not 'any cloud'")
check(ca.jd_cloud("Experience with any major cloud provider.")["any_cloud"] is False, "one-cloud-free text: nothing mentioned -> not flagged")
blk = ca.prompt_block(j, ["AWS"])
check("accepts any of" in blk and "do NOT push or add another cloud" in blk, f"AI told the candidate's cloud is a full match: {blk[:160]}")
only_plus = ca.jd_cloud("Requirements: 5+ years SQL, Power BI, Python, Azure Synapse a plus, strong communication.")
check(only_plus["primary"] is None and only_plus["optional_only"] == ["Azure"], f"a cloud named only as 'a plus' is not required: {only_plus}")
check(ca.clouds_in("Pipelines on AWS EMR, S3 and Glue; some BigQuery") == ["AWS", "GCP"], "resume clouds, most first")

# ---- validation of sections
AWS_RESUME = """RAVI CLOUDTEST
PROFESSIONAL SUMMARY
Data Engineer with 8 years building pipelines on AWS using Spark and Python.
TECHNICAL SKILLS
Cloud: AWS (S3, EMR, Glue, Lambda functions, Redshift)
PROFESSIONAL EXPERIENCE
Example Bank Testco | Data Engineer | Jan 2022 - Present
Built batch pipelines with Spark on AWS EMR and S3.
Example Retail Testco | Data Engineer | Mar 2019 - Dec 2021
Migrated jobs to Azure Data Factory.
EDUCATION
MS Computer Science, 2017"""
v = ca.validate(AWS_RESUME, "Azure")
check(not v["ok"] and v["sections"] == {"summary": False, "skills": False, "recent_project": False},
      f"older Azure job does not count as the most recent project: {v['sections']}")
check("mainly on AWS" in v["message"] and "do not relabel AWS work as Azure" in v["message"], f"message: {v['message']}")
eq = {e["from"]: e["to"] for e in v["equivalents"]}
check(eq.get("EC2") == "Azure Virtual Machines" and eq.get("S3") == "Blob Storage / ADLS Gen2" and eq.get("Lambda") == "Azure Functions"
      and eq.get("EKS") == "AKS" and eq.get("Glue") == "Data Factory (ADF)", f"equivalents: {eq}")
ok = ca.validate(AWS_RESUME.replace("on AWS using", "on AWS and Azure using").replace("Cloud: AWS", "Cloud: Azure (ADF), AWS")
                 .replace("on AWS EMR and S3.", "on AWS EMR and S3, orchestrated with Azure Data Factory."), "Azure")
check(ok["ok"] and ok["equivalents"] == [], f"all three sections show Azure -> ok: {ok['sections']}")
check(ca.validate(AWS_RESUME, None)["ok"], "no cloud in the JD -> nothing to check")

# ---- the optimizer: prompt carries the target cloud + the no-relabel rule; result has the check
prompts = []
ANALYSIS = {"initial_match_percentage": 70, "match_breakdown": {"mandatory_skills": 28, "recent_project_relevance": 17, "domain_experience": 12,
            "tools_frameworks_cloud": 6, "certifications_education": 5, "location_work_authorization": 2}, "domain_detected": "Banking",
            "strong_match_skills": ["Spark"], "partial_match_skills": [], "mandatory_missing_skills": ["Azure Data Factory"],
            "preferred_missing_skills": [], "risky_skills_avoided": ["AKS - no Kubernetes background"],
            "skills_to_add": {"summary": [], "technical_skills": [], "projects": [], "environment": []},
            "ats_optimization_notes": [], "target_match_percentage": 72}


def fake_generate(client, types, system, prompt, want_json, max_tokens):
    prompts.append(prompt)
    return (json.dumps(ANALYSIS), False, "stub") if want_json else ("[3] Data Engineer with 8 years building batch pipelines on AWS using Spark and Python.", False, "stub")


rb._generate = fake_generate
rb.gemini_configured = lambda: True
res = rb.optimize_resume_for_jd(AWS_RESUME, AZURE_JD)
check(all("CLOUD ALIGNMENT" in p and "primary cloud is Azure" in p and "NEVER change which cloud a past project used" in p for p in prompts),
      "both AI steps get the target cloud and the no-relabel rule")
check(res["jd_cloud"]["primary"] == "Azure" and res["cloud_check"]["ok"] is False and res["cloud_check"]["equivalents"],
      f"result flags the mismatch: {res.get('cloud_check', {}).get('message')}")
check("Azure" not in res.get("updated_resume_text", "").split("PROFESSIONAL EXPERIENCE")[1].split("Example Retail")[0],
      "the AWS project was not relabelled")
prompts.clear()
rb.optimize_resume_for_jd(AWS_RESUME, "Java developer with Spring Boot and React.")
check(not any("CLOUD ALIGNMENT" in p for p in prompts), "no cloud in the JD -> no cloud block")

# ---- /api/jd/cloud
uid = lambda u: u["id"] if isinstance(u, dict) else u  # noqa: E731
rec = {"id": uid(models.create_user("Cloud Rec", "cloud-rec@example.invalid", "Cloud-1", role="Recruiter")), "name": "Cloud Rec", "role": "Recruiter"}
other = {"id": uid(models.create_user("Cloud Other", "cloud-other@example.invalid", "Cloud-2", role="Recruiter")), "name": "Cloud Other", "role": "Recruiter"}
cid = models.create_candidate("Ravi Cloudtest", "ravi-cloud@example.invalid", title="Data Engineer", primary_skills="Spark, Python, AWS, S3, EMR",
                              assigned_user_id=rec["id"])
client = app_module.app.test_client()
check(client.post("/api/jd/cloud", json={"jd_text": AZURE_JD}).status_code == 401, "login required")
with client.session_transaction() as s:
    s["user"] = rec
d = client.post("/api/jd/cloud", json={"jd_text": AZURE_JD, "candidate_id": cid}).get_json()
check(d["primary"] == "Azure" and d["consultant_clouds"] == ["AWS"] and d["mismatch"] is True, f"mismatch for own consultant: {d}")
with client.session_transaction() as s:
    s["user"] = other
d = client.post("/api/jd/cloud", json={"jd_text": AZURE_JD, "candidate_id": cid}).get_json()
check(d["consultant_clouds"] == [] and d["mismatch"] is False, "another recruiter's consultant is not read")

if failures:
    print(f"FAIL: {len(failures)} check(s) failed:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("PASS: cloud alignment - JD primary cloud, Java lambdas ignored, section check, equivalents as talking points, AI told target + no relabelling, /api/jd/cloud.")
