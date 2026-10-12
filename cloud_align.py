"""Which cloud a JD needs (AWS / Azure / GCP), which clouds a resume shows, and an honest alignment check.

- detect(text)            -> how strongly each cloud appears (names + their services: AKS, ADF, EC2, BigQuery...)
- jd_cloud(jd)            -> the JD's PRIMARY cloud: mentions in a "required / must / primary" line weigh
                             3x, "nice to have / plus / preferred" lines weigh 0.5x; plus every cloud mentioned
- prompt_block(...)       -> what the optimizer tells the AI: the target cloud, judged by the master prompt's
                             Skill Classification Engine. It may add that cloud only where it is realistic and
                             supportable for this candidate - it must NEVER relabel past work (an AWS project
                             stays an AWS project): that would be a false claim to the client.
- validate(resume, cloud) -> does the summary / skills / most recent project reflect the JD's cloud? If not,
                             the recruiter is warned, with equivalent services (EC2 ~ Azure VMs, ...) as talking
                             points to CONFIRM with the consultant - never written into the resume automatically.
"""
import re
from typing import Dict, List, Optional

CLOUDS = ["AWS", "Azure", "GCP"]

_TERMS = {
    "AWS": [r"\bAWS\b", r"amazon web services", r"\bEC2\b", r"\bS3\b", r"\bAWS Lambda\b", r"\bLambda functions?\b", r"\bEKS\b", r"\bECS\b",
            r"\bAWS Glue\b", r"\bRedshift\b", r"\bEMR\b", r"\bDynamoDB\b", r"\bCloudFormation\b", r"\bCloudWatch\b", r"\bKinesis\b",
            r"\bAthena\b", r"\bSageMaker\b", r"\bAWS RDS\b|\bAmazon RDS\b", r"\bSQS\b", r"\bSNS\b", r"\bStep Functions\b", r"\bAurora\b"],
    "Azure": [r"\bAzure\b", r"\bAKS\b", r"\bADF\b", r"\bData Factory\b", r"\bSynapse\b", r"\bCosmos ?DB\b", r"\bBlob Storage\b", r"\bADLS\b",
              r"\bARM templates?\b", r"\bBicep\b", r"\bEvent Hubs?\b", r"\bService Bus\b", r"\bHDInsight\b", r"\bEntra ID\b", r"\bLogic Apps\b"],
    "GCP": [r"\bGCP\b", r"\bGoogle Cloud\b", r"\bBigQuery\b", r"\bGKE\b", r"\bCloud Run\b", r"\bCloud Functions\b", r"\bPub/?Sub\b",
            r"\bDataflow\b", r"\bDataproc\b", r"\bCloud Composer\b", r"\bVertex AI\b", r"\bSpanner\b", r"\bBigtable\b"],
}
_RX = {c: re.compile("|".join(t), re.I) for c, t in _TERMS.items()}
_REQUIRED_RE = re.compile(r"required|must|mandatory|primary|strong (experience|knowledge)|hands[- ]on|expert|minimum|\d+\+? ?years", re.I)
# "Azure, AWS, or GCP" / "AWS or Azure" / "AWS/Azure/GCP" / "any major cloud": the JD accepts ANY of them,
# so no single cloud is its "primary" one (an AWS candidate is not a mismatch for it).
_CLOUD_NAME = r"(?:AWS|Amazon Web Services|Azure|Microsoft Azure|GCP|Google Cloud(?: Platform)?)"
_ALTERNATIVES_RE = re.compile(_CLOUD_NAME + r"(?:\s*(?:,\s*(?:or\b|and/or\b)?|/|\bor\b|\band/or\b)\s*" + _CLOUD_NAME + r")+", re.I)
_ANY_CLOUD_RE = re.compile(r"any (?:major |public |leading )?cloud|cloud[- ]agnostic|one or more (?:of the )?(?:major )?cloud|"
                           r"at least one (?:major |public )?cloud", re.I)
_OPTIONAL_RE = re.compile(r"nice to have|is a plus|\bplus\b|preferred|bonus|good to have|desirable|familiarity", re.I)

# Equivalent services, one row per capability: (capability, AWS, Azure, GCP)
EQUIVALENTS = [
    ("Virtual machines", "EC2", "Azure Virtual Machines", "Compute Engine"),
    ("Object storage", "S3", "Blob Storage / ADLS Gen2", "Cloud Storage"),
    ("Serverless functions", "Lambda", "Azure Functions", "Cloud Functions"),
    ("Managed Kubernetes", "EKS", "AKS", "GKE"),
    ("ETL / data integration", "Glue", "Data Factory (ADF)", "Dataflow / Data Fusion"),
    ("Data warehouse", "Redshift", "Synapse Analytics", "BigQuery"),
    ("NoSQL database", "DynamoDB", "Cosmos DB", "Firestore / Bigtable"),
    ("Streaming", "Kinesis", "Event Hubs", "Pub/Sub"),
    ("Messaging queue", "SQS", "Service Bus", "Pub/Sub"),
    ("Hadoop / Spark clusters", "EMR", "HDInsight / Databricks", "Dataproc"),
    ("Infrastructure as code", "CloudFormation", "ARM / Bicep", "Deployment Manager"),
    ("Monitoring", "CloudWatch", "Azure Monitor", "Cloud Monitoring"),
    ("Machine learning", "SageMaker", "Azure Machine Learning", "Vertex AI"),
]


def detect(text: str) -> Dict[str, int]:
    """How many times each cloud (by name or service) appears."""
    text = text or ""
    return {c: len(_RX[c].findall(text)) for c in CLOUDS}


def any_cloud(jd_text: str) -> bool:
    """True when the JD names clouds as alternatives ("AWS, Azure, or GCP") or asks for any cloud."""
    for line in re.split(r"[\n.;]", jd_text or ""):
        if _OPTIONAL_RE.search(line):        # "AWS or GCP is a plus" is not "any cloud is fine"
            continue
        if _ANY_CLOUD_RE.search(line):
            return True
        for m in _ALTERNATIVES_RE.finditer(line):
            names = {re.sub(r"amazon web services", "aws", re.sub(r"microsoft azure", "azure", re.sub(r"google cloud.*", "gcp", n.lower())))
                     for n in re.findall(_CLOUD_NAME, m.group(0), re.I)}
            if len(names) >= 2 and re.search(r"\bor\b|/", m.group(0), re.I):
                return True
    return False


def jd_cloud(jd_text: str) -> Dict:
    """{'primary': 'Azure' | None, 'mentioned': [...], 'scores': {...}, 'any_cloud': bool} for a JD."""
    scores = {c: 0.0 for c in CLOUDS}
    firm = {c: 0.0 for c in CLOUDS}          # mentions outside "nice to have / a plus" lines
    for line in re.split(r"[\n.;]", jd_text or ""):
        hits = detect(line)
        if not any(hits.values()):
            continue
        optional = bool(_OPTIONAL_RE.search(line))
        weight = 0.5 if optional else (3.0 if _REQUIRED_RE.search(line) else 1.0)
        for c, n in hits.items():
            scores[c] += n * weight
            if not optional:
                firm[c] += n * weight
    mentioned = [c for c in CLOUDS if scores[c] > 0]
    # A cloud named ONLY as "a plus / nice to have" is not the JD's required cloud - no mismatch warning for it.
    required = [c for c in CLOUDS if firm[c] > 0]
    primary = max(required, key=lambda c: scores[c]) if required else None
    accepts_any = len(mentioned) >= 2 and any_cloud(jd_text)
    if accepts_any:
        primary = None                       # e.g. "Manage cloud environments on Azure, AWS, or GCP"
    return {"primary": primary, "mentioned": mentioned, "optional_only": [c for c in mentioned if c not in required],
            "scores": {c: round(s, 1) for c, s in scores.items()}, "any_cloud": accepts_any}


def clouds_in(text: str) -> List[str]:
    """Clouds a resume / profile shows, most-mentioned first."""
    counts = detect(text)
    return [c for c in sorted(CLOUDS, key=lambda c: -counts[c]) if counts[c]]


def equivalents(source: str, target: str) -> List[Dict]:
    """Equivalent services between two clouds - talking points, never auto-inserted."""
    if source not in CLOUDS or target not in CLOUDS or source == target:
        return []
    si, ti = CLOUDS.index(source) + 1, CLOUDS.index(target) + 1
    return [{"capability": row[0], "from": row[si], "to": row[ti]} for row in EQUIVALENTS]


def prompt_block(jd_info: Dict, resume_clouds: List[str]) -> str:
    """Cloud instructions added to the optimizer prompt ('' when the JD names no cloud)."""
    target = jd_info.get("primary")
    if jd_info.get("any_cloud"):
        return (f"CLOUD ALIGNMENT (from the app): the JD accepts any of {', '.join(jd_info.get('mentioned') or CLOUDS)} "
                f"(they are alternatives). The resume shows: {', '.join(resume_clouds) or 'no specific cloud'}. Treat the candidate's "
                f"own cloud as a full match - do NOT push or add another cloud to reach this JD, and never relabel past work. "
                f"Emphasise the candidate's real cloud and cloud-agnostic skills (Terraform, Kubernetes, CI/CD) truthfully.")
    if not target:
        return ""
    others = [c for c in jd_info.get("mentioned", []) if c != target]
    return (
        f"CLOUD ALIGNMENT (from the app): the JD's primary cloud is {target}"
        + (f" (it also mentions {', '.join(others)}, which matter less)" if others else "")
        + f". The resume currently shows: {', '.join(resume_clouds) or 'no specific cloud'}. "
        f"Prioritise {target} in the analysis and the summary / skills / most recent project. "
        f"Judge every {target} skill with the Skill Classification Engine and add {target} items only where they are "
        f"'Missing But Addable' for this candidate (realistic and supportable). NEVER change which cloud a past project used "
        f"and never rename one cloud's services as another's (an AWS project stays an AWS project). Cloud-agnostic "
        f"experience the candidate really has (Terraform, Kubernetes, Docker, CI/CD, Spark) may be emphasised truthfully."
    )


_HEADINGS = {
    "summary": re.compile(r"summary|profile|objective|about", re.I),
    "skills": re.compile(r"skill|technolog|technical|tools|competenc", re.I),
    "experience": re.compile(r"experience|employment|work history|projects?|professional background", re.I),
}


def _sections(resume_text: str) -> Dict[str, str]:
    """Summary, skills and the MOST RECENT job/project block (first block of the experience section)."""
    out = {"summary": [], "skills": [], "recent_project": []}
    current, jobs_seen = None, 0
    for raw in (resume_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_heading = len(line) <= 45 and not line.endswith(".") and (line.isupper() or line.endswith(":") or len(line.split()) <= 4)
        key = next((k for k, rx in _HEADINGS.items() if rx.search(line)), None) if is_heading else None
        if key:
            current, jobs_seen = key, 0
            continue
        if is_heading and line.isupper():
            current = None                      # EDUCATION, CERTIFICATIONS ... end the previous section
            continue
        if current == "experience":
            if re.search(r"(19|20)\d\d\s*(-|–|to)\s*([A-Za-z]{3,9}\.?\s*)?((19|20)\d\d|present|current|till date)", line, re.I):
                jobs_seen += 1                  # a job header line with a date range
            if jobs_seen <= 1:
                out["recent_project"].append(line)
        elif current in out:
            out[current].append(line)
    return {k: "\n".join(v) for k, v in out.items()}


def validate(resume_text: str, target: Optional[str]) -> Dict:
    """Does the (optimized) resume reflect the JD's cloud in its summary, skills and most recent project?"""
    if not target:
        return {"target": None, "ok": True, "sections": {}, "message": ""}
    sec = _sections(resume_text)
    sections = {k: bool(_RX[target].search(v)) for k, v in sec.items()}
    ok = all(sections.values())
    resume_clouds = clouds_in(resume_text)
    source = next((c for c in resume_clouds if c != target), None)
    missing = [k.replace("_", " ") for k, v in sections.items() if not v]
    if ok:
        msg = f"The JD's primary cloud is {target}, and the resume shows {target} in the summary, skills and most recent project."
    else:
        listed = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " and " + missing[-1]
        msg = (f"The JD's primary cloud is {target}, but the resume's {listed} "
               f"{'does' if len(missing) == 1 else 'do'} not show {target}"
               + (f" (its experience is mainly on {source})" if source else "")
               + f". Confirm the consultant's real {target} experience before submitting - do not relabel {source or 'other'} work as {target}.")
    return {"target": target, "ok": ok, "sections": sections, "resume_clouds": resume_clouds, "message": msg,
            "equivalents": equivalents(source, target) if (source and not ok) else []}
