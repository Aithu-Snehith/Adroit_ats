import re
import os
import io
import base64
import difflib
import json
import time
import hashlib
import logging
import requests
import docx
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pypdf

import config
import docx_editor
import cloud_align

logger = logging.getLogger("resume_bot")

# Free-tier Gemini model for the real AI rewrite (aistudio.google.com/apikey - no card
# needed). Kept on the "-latest" alias so it keeps pointing at a free-tier-eligible Flash
# model as Google updates what that alias means, rather than pinning a dated version here.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
# Per Gemini call. Both AI steps + a backup must finish inside gunicorn's 240 s request limit.
GEMINI_TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")) * 1000

# Claude (Anthropic) - the MAIN AI when ANTHROPIC_API_KEY is set: it answers both steps, and Gemini's
# chain, then OpenRouter / Grok, are only tried if Claude fails. Paid per use. CLAUDE_MODEL can be
# changed on Render (e.g. claude-sonnet-5-5 costs about half) without a code change.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")          # low | medium | high
# Per Claude call; a slow answer falls through to Gemini, so both steps stay inside gunicorn's 240 s.
CLAUDE_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "90"))
# Models that take Anthropic's server-side refusal fallback (a declined request is re-run on another model).
_CLAUDE_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

# Optional backups for when Gemini's whole free-tier chain fails (see _generate). Never the
# default: Gemini answers every ordinary request, so a normal day costs nothing extra. The FREE
# one (OpenRouter) is tried first, the paid one (xAI) only if that also fails or isn't configured.
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
XAI_API_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.3")


def openrouter_configured() -> bool:
    return bool((config.OPENROUTER_API_KEY or "").strip())


def xai_configured() -> bool:
    return bool((config.XAI_API_KEY or "").strip())


def claude_configured() -> bool:
    return bool((getattr(config, "ANTHROPIC_API_KEY", "") or "").strip())


def ai_configured() -> bool:
    """Any AI that can run the two-step optimization (Claude main, or Gemini)."""
    return claude_configured() or gemini_configured()

# Master domains catalog
DOMAINS = {
    "Fintech / Banking": ["bank", "fintech", "payment", "trading", "settlement", "clearing", "credit", "wealth", "lending", "fraud", "risk", "capital", "financial"],
    "Healthcare": ["health", "hospital", "clinical", "patient", "ehr", "hipaa", "medical", "pharma", "biotech", "care"],
    "E-Commerce / Retail": ["retail", "ecommerce", "e-commerce", "cart", "checkout", "inventory", "catalog", "supply chain", "order"],
    "Telecom": ["telecom", "5g", "network", "voip", "fiber", "carrier", "broadband"],
    "SaaS / Cloud Platform": ["saas", "multi-tenant", "b2b", "subscription", "platform", "cloud-native", "api platform"],
    "Insurance": ["insurance", "policy", "claims", "underwriting", "actuarial"]
}

# Technical keywords dictionary. Two jobs: (1) the keyword-scan fallback when the AI is down, and
# (2) the "unapproved technology" guard - a technology named here that the original resume lacks
# and the analysis step did not approve is refused. So this list is deliberately about real
# technologies, not soft words ('swift', 'helm', 'go' would false-alarm on ordinary sentences).
TECH_KEYWORDS = [
    "Java", "Java 21", "Java 17", "Spring Boot", "Spring Cloud", "Microservices", "REST API", "GraphQL",
    "Kafka", "RabbitMQ", "ActiveMQ", "AWS", "Amazon Web Services", "Azure", "GCP", "Google Cloud",
    "Docker", "Kubernetes", "EKS", "AKS", "Terraform", "CI/CD", "Jenkins", "GitLab CI", "GitHub Actions",
    "Python", "FastAPI", "Django", "Flask", "PyTorch", "TensorFlow", "LangChain", "LLM", "RAG", "Vector DB",
    "React", "React 19", "Next.js", "TypeScript", "JavaScript", "Angular", "Vue.js", "Node.js", "Tailwind CSS",
    "PostgreSQL", "MySQL", "Oracle", "MongoDB", "Cassandra", "Redis", "Elasticsearch", "Snowflake", "dbt",
    "Airflow", "Spark", "Hadoop", "SQL", "gRPC", "Prometheus", "Grafana", "Splunk", "ArgoCD", "Linux",
    # AI / automation / no-code
    "n8n", "Zapier", "Make.com", "Airtable", "OpenAI", "GPT-4", "GPT-4o", "Claude", "Gemini", "LangGraph", "CrewAI",
    "AutoGen", "Pinecone", "ChromaDB", "Weaviate", "Hugging Face", "Prompt Engineering", "MCP", "Webhooks",
    "WhatsApp Business API", "Twilio", "Power Automate", "Retool", "HubSpot", "Salesforce",
    # more general engineering
    ".NET", "C#", "PHP", "Laravel", "Ruby on Rails", "Kotlin", "Selenium", "Playwright", "Cypress", "JUnit",
    "Hibernate", "Maven", "Gradle", "Ansible", "Azure DevOps", "Databricks", "BigQuery", "Redshift",
    "Power BI", "Tableau", "OAuth", "JWT",
]
# A keyword-scan percentage is only shown when the JD names at least this many of them.
MIN_JD_TECH = 3

def extract_text_from_file_bytes(file_bytes, filename, strict=False):
    """Extracts text from PDF, DOCX, or plain text bytes. With strict=True a file that can't be
    parsed returns '' instead of its raw bytes decoded as text (which for a broken .docx/.pdf is
    just binary garbage that would then be treated as the resume)."""
    name = filename.lower()
    text = ""
    try:
        if name.endswith(".docx"):
            # Same paragraph order (tables included, where they sit on the page) that the
            # in-place editor shows the AI, so the text box matches what gets optimized.
            text = docx_editor.extract_text(file_bytes)
        elif name.endswith(".pdf"):
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            text = "\n\n".join(pages_text)
        else:
            text = file_bytes.decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"Error extracting text from {filename}: {e}")
        text = "" if strict else file_bytes.decode("utf-8", errors="ignore")
    return text.strip()

def detect_domain(text):
    text_lower = text.lower()
    best_domain = "Enterprise SaaS & Cloud"
    highest_score = 0

    for domain, keywords in DOMAINS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > highest_score:
            highest_score = score
            best_domain = domain

    return best_domain

def extract_skills_from_text(text):
    # Same boundary rule as the unapproved-technology guard, so 'C#', '.NET' and 'Node.js' are found
    # too (a plain \b can't sit next to '#' or a leading '.').
    found = [tech for tech in TECH_KEYWORDS if _tech_present(tech, text)]
    return list(dict.fromkeys(found))

def gemini_configured() -> bool:
    return bool((config.GEMINI_API_KEY or "").strip())


def _explain_gemini_error(ex: Exception) -> str:
    """Short, user-safe reason a Gemini call failed - shown in the result so a problem (bad
    key, exhausted free quota, wrong model, timeout) is diagnosable instead of the page
    silently doing nothing. Never includes the API key itself."""
    msg = str(ex)
    for key in ((config.GEMINI_API_KEY or "").strip(), (getattr(config, "ANTHROPIC_API_KEY", "") or "").strip()):
        if key:
            msg = msg.replace(key, "***")
    low = msg.lower()
    if isinstance(ex, ClaudeError):
        if "401" in msg or "authentication" in low or "invalid x-api-key" in low:
            return "Anthropic rejected the API key - check ANTHROPIC_API_KEY on Render was copied correctly"
        if "credit balance" in low or "billing" in low:
            return "the Anthropic account is out of credit - add credit at console.anthropic.com"
        if "429" in msg or "rate limit" in low:
            return "Claude's rate limit was hit - click Optimize again in a minute"
        if "404" in msg or "not_found" in low:
            return f"Claude model '{CLAUDE_MODEL}' isn't available on this key - check CLAUDE_MODEL"
        if _is_timeout_error(ex):
            return f"Claude took too long to answer (over {int(CLAUDE_TIMEOUT_SECONDS)} seconds) - click Optimize again"
        return "Claude call failed: " + " ".join(msg.split())[:140]
    if "429" in msg or "resource_exhausted" in low or "quota" in low:
        return "Gemini's free daily quota is used up - it resets at midnight Pacific time"
    if "api key" in low or "api_key" in low or "401" in msg or "403" in msg or "permission_denied" in low or "unauthenticated" in low:
        return "Google rejected the API key - check GEMINI_API_KEY was copied correctly"
    if "404" in msg or "not found" in low or "is not supported" in low:
        return f"Gemini model '{GEMINI_MODEL}' isn't available on this key - set GEMINI_MODEL to a current model name"
    if "timeout" in low or "timed out" in low or "deadline" in low:
        return ("Gemini (free tier) took too long to answer (over 45 seconds)"
                + (" and the backup AI failed too" if (openrouter_configured() or xai_configured()) else " and no backup AI is configured")
                + " - click Optimize again in a minute")
    if _is_transient_gemini_error(ex):
        return ("Google's Gemini is overloaded right now (503 - high demand), even after retrying and switching to backup models. "
                "Nothing is wrong with your resume or the JD - click Optimize again in a minute or two")
    return "Gemini call failed: " + " ".join(msg.split())[:140]


def _is_transient_gemini_error(ex: Exception) -> bool:
    low = str(ex).lower()
    return any(t in low for t in ("503", "unavailable", "overloaded", "high demand", "500 internal", "temporarily"))


def _is_timeout_error(ex: Exception) -> bool:
    low = (str(ex) + " " + ex.__class__.__name__).lower()
    return "timeout" in low or "timed out" in low or "deadline" in low


def _is_quota_error(ex: Exception) -> bool:
    low = str(ex).lower()
    return "429" in low or "resource_exhausted" in low or "quota" in low


def _is_model_missing_error(ex: Exception) -> bool:
    low = str(ex).lower()
    return "404" in low or "not found" in low or "is not supported" in low


# The free tier's capacity and daily quota are per MODEL, so when the preferred model is overloaded
# (503) or out of quota (429), the next one usually still answers. Order = best first.
_DEFAULT_FALLBACK_MODELS = "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash-lite"


def _model_chain():
    """Models to try, in order: GEMINI_MODEL first, then GEMINI_FALLBACK_MODELS (comma separated;
    set it to an empty value to turn fallbacks off)."""
    chain = [GEMINI_MODEL]
    for m in os.getenv("GEMINI_FALLBACK_MODELS", _DEFAULT_FALLBACK_MODELS).split(","):
        m = m.strip()
        if m and m not in chain:
            chain.append(m)
    return chain


# --------------------------------------------------------------------------------------
# The recruiter's own master prompt is the spec. It is sent to Gemini verbatim (so editing
# MASTER_RESUME_PROMPT.md changes the optimizer's behaviour with no code change); the code
# below only adds a machine-readable output contract and enforces the rules it can verify.
# --------------------------------------------------------------------------------------
MASTER_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MASTER_RESUME_PROMPT.md")


def load_master_prompt() -> str:
    try:
        with open(MASTER_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as ex:
        logger.error(f"Could not read {MASTER_PROMPT_FILE}: {ex}")
        return ""


# Two calls per optimization, so each does one job well:
#   STEP 1 - analysis (small JSON): match %, missing/risky skills, and the list of skills that
#            pass the master prompt's Skill Classification Engine and may be added.
#   STEP 2 - the COMPLETE updated resume as plain text (item 6 of the master prompt's final
#            output format). Plain text, not a JSON string, so a long resume can't break the
#            answer with a stray quote/newline or run out of room next to the analysis.
# The master prompt itself is sent verbatim as the system prompt in both; the contracts below
# only say how this app reads each answer.
_ANALYSIS_CONTRACT = """=== HOW THIS APP READS YOUR ANSWER - STEP 1 OF 2: ANALYSIS ONLY (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above for this step) ===
One decision rule is missing from the MATCH-BASED DECISION RULES above: BELOW 60% MATCH -> Reject (do not optimize).
Do NOT write or rewrite the resume in this step - a second step writes the complete updated resume from your analysis.

Respond with ONE JSON object and nothing else - no markdown fences, no commentary before or after - using exactly these keys:
{
  "initial_match_percentage": <integer 0-100, realistic and NOT inflated>,
  "match_breakdown": {
    "mandatory_skills": <0-40>, "recent_project_relevance": <0-25>, "domain_experience": <0-15>,
    "tools_frameworks_cloud": <0-10>, "certifications_education": <0-5>, "location_work_authorization": <0-5>
  },
  "domain_detected": "<domain>",
  "strong_match_skills": ["..."],
  "partial_match_skills": ["..."],
  "mandatory_missing_skills": ["..."],
  "preferred_missing_skills": ["..."],
  "risky_skills_avoided": ["skills you will NOT add, and why in a few words"],
  "skills_to_add": {"summary": ["..."], "technical_skills": ["..."], "projects": ["..."], "environment": ["..."]},
  "ats_optimization_notes": ["Keywords optimized: ...", "Domain alignment improvements: ...", "Recent project enhancements: ..."],
  "target_match_percentage": <integer 0-100>
}
match_breakdown must add up to initial_match_percentage.
skills_to_add lists ONLY skills that pass your Skill Classification Engine as 'Missing But Addable' (realistic, supportable, timeline-valid, role-appropriate for this candidate), each under the section where it will go. The next step may add NO technology that is not in this list or already in the resume, so leave out anything risky. Use empty lists when nothing should be added."""

_REWRITE_INTRO = """=== HOW THIS APP READS YOUR ANSWER - STEP 2 OF 2: THE COMPLETE UPDATED RESUME (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above for this step) ===
The analysis and the match decision are already done - see APPROVED ANALYSIS in the message. Now provide item 6 of the final output format: the COMPLETE updated resume, preserving the original format and structure.
Return ONLY the resume as plain text - no JSON, no markdown fences, no analysis, no commentary before or after.

Rules:
- Apply the enhancement level from the approved match decision (Moderate / Strong / Minimal), following every STRICT RESUME SAFETY RULE, the PROJECT ENHANCEMENT LOGIC and the SKILL INSERTION RULES above: the changes belong mainly in the Professional Summary, Technical Skills, and the most recent project with its Environment line.
- You may add ONLY the skills listed under 'Skills you may add' in the approved analysis. Never add a technology that is neither in that list nor already in the resume, and never add anything under 'Skills NOT to add'.
- Everything you do not need to change stays EXACTLY as it is, word for word. Never change a date, an employer, a job title, education, contact details or the total years of experience, and never state a new number of years for any technology.
@@FORMAT@@"""

_REWRITE_FORMAT_TEXT = """- Keep the resume's section order, headings and bullet structure exactly as they are. Begin with the resume's first line (the candidate's name)."""

_REWRITE_FORMAT_DOCX = """- The resume is given as the NUMBERED PARAGRAPHS of the candidate's ORIGINAL Word file, for example "[12] (bullet) Built batch pipelines". This app writes your text back into that file paragraph by paragraph, which is how the original fonts, layout and bullets are preserved. So return the COMPLETE resume in exactly the same form: every paragraph, in the same order, each on its own line that starts with its own number in square brackets, for example "[12] Built batch pipelines". Do not copy the tags in parentheses. Repeat every unchanged paragraph exactly as given.
- A paragraph tagged 'heading/title line' or 'locked' must be repeated exactly as given.
- To add a NEW paragraph (for example a new bullet), write it on its own line as "[+] text" directly after the paragraph it should follow. It copies that paragraph's formatting, so add new bullets only right after a bullet of the same section. Never delete a paragraph."""

_REWRITE_CONTRACT_TEXT = _REWRITE_INTRO.replace("@@FORMAT@@", _REWRITE_FORMAT_TEXT)
_REWRITE_CONTRACT_DOCX = _REWRITE_INTRO.replace("@@FORMAT@@", _REWRITE_FORMAT_DOCX)

# Speed: step 2 used to repeat the WHOLE resume word for word - writing that long answer was most of
# the wait. In "changes" mode (default) the AI returns only the paragraphs it changes; every other
# paragraph stays exactly as it is. RESUME_REWRITE_MODE=full brings back the old behaviour (e.g. to
# compare timings on the live site).
REWRITE_MODE = "full" if os.getenv("RESUME_REWRITE_MODE", "changes").strip().lower() == "full" else "changes"

_REWRITE_CHANGES_CONTRACT = """=== HOW THIS APP READS YOUR ANSWER - STEP 2 OF 2: ONLY THE CHANGED PARAGRAPHS (this replaces the 'FINAL OUTPUT FORMAT' and 'OUTPUT' wording above for this step) ===
The analysis and the match decision are already done - see APPROVED ANALYSIS in the message. The resume is given as NUMBERED PARAGRAPHS, for example "[12] (bullet) Built batch pipelines". This app applies your changes paragraph by paragraph, so the original formatting is preserved.

Return ONLY the paragraphs you change - never repeat a paragraph you leave unchanged:
- A changed paragraph: its number in square brackets, then its complete new text, on one line, for example "[12] Built batch pipelines on AWS Glue and Spark". Do not copy the tags in parentheses.
- A NEW paragraph (for example a new bullet): "[+12] text" puts it directly after paragraph 12 with that paragraph's formatting, so add new bullets only after a bullet of the same section.
- Plain text only - no JSON, no markdown fences, no commentary before or after.

Rules:
- Apply the enhancement level from the approved match decision (Moderate / Strong / Minimal), following every STRICT RESUME SAFETY RULE, the PROJECT ENHANCEMENT LOGIC and the SKILL INSERTION RULES above: the changes belong mainly in the Professional Summary, Technical Skills, and the most recent project with its Environment line.
- You may add ONLY the skills listed under 'Skills you may add' in the approved analysis. Never add a technology that is neither in that list nor already in the resume, and never add anything under 'Skills NOT to add'.
- Never change a paragraph tagged 'heading/title line' or 'locked'. Never change a date, an employer, a job title, education, contact details or the total years of experience, and never state a new number of years for any technology. Never delete a paragraph."""


def _text_paragraphs(resume_text: str):
    """A plain-text resume as numbered 'paragraphs' (its non-empty lines) for the changes-only rewrite."""
    # Heading lines (and the name on the first line) are tagged "do not edit", like in a Word file.
    return [{"index": k, "line": i, "text": line, "bullet": False, "heading": k == 0 or _looks_like_heading(line.strip()), "locked": False}
            for k, (i, line) in enumerate((i, l) for i, l in enumerate(resume_text.split("\n")) if l.strip())]


def _apply_text_changes(resume_text: str, raw: str, truncated: bool) -> str:
    """Applies a changes-only answer to a plain-text resume: replaced lines and inserted lines."""
    lines = resume_text.split("\n")
    paras = _text_paragraphs(resume_text)
    entries = docx_editor.parse_numbered(raw)
    if truncated and entries:
        entries = entries[:-1]
    edits, _ = docx_editor.edits_from_rewrite(entries, paras)
    pos = {p["index"]: p["line"] for p in paras}
    headings = {p["index"] for p in paras if p["heading"]}
    inserts = {}
    for e in edits:
        if e["op"] == "replace" and e["paragraph"] in headings:
            continue                                   # a heading / the name is never rewritten
        if e["op"] == "replace":
            lines[pos[e["paragraph"]]] = e["new_text"]
        else:
            inserts.setdefault(pos[e["paragraph"]], []).append(e["new_text"])
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        out.extend(inserts.get(i, []))
    return "\n".join(out)


# Speed: the same resume + JD (e.g. "Re-optimize") reuses step 1's analysis instead of asking again.
_ANALYSIS_CACHE = {}
_ANALYSIS_CACHE_TTL = 6 * 3600
_ANALYSIS_CACHE_MAX = 200


def _analysis_cache_key(master: str, jd_text: str, resume_text: str, custom: str) -> str:
    return hashlib.sha256("\x1f".join((master, jd_text, resume_text, custom)).encode("utf-8")).hexdigest()


def _cached_analysis(key: str):
    hit = _ANALYSIS_CACHE.get(key)
    if hit and time.time() - hit[2] < _ANALYSIS_CACHE_TTL:
        return hit[0], hit[1]
    _ANALYSIS_CACHE.pop(key, None)
    return None


def _store_analysis(key: str, raw: str, model: str):
    if len(_ANALYSIS_CACHE) >= _ANALYSIS_CACHE_MAX:
        _ANALYSIS_CACHE.pop(min(_ANALYSIS_CACHE, key=lambda k: _ANALYSIS_CACHE[k][2]))
    _ANALYSIS_CACHE[key] = (raw, model, time.time())


# Speed: JD boilerplate (benefits, EEO / legal statements, "about us") is sent to the AI twice and
# says nothing about the skills. Whole paragraphs that are ONLY such boilerplate are dropped; a
# paragraph that also mentions a requirement is kept. Very long JDs are capped.
_JD_BOILERPLATE_RE = re.compile(
    r"equal (employment )?opportunity|\bEEO\b|without regard to (race|color|religion)|reasonable accommodation|"
    r"affirmative action|e-verify|privacy (notice|policy)|401\(?k\)?|paid time off|\bPTO\b|dental|vision (insurance|coverage)|"
    r"medical (insurance|benefits)|benefits (include|package)|we offer|perks|about (us|the company|our company)|"
    r"pay transparency|applicants with arrest|drug[- ]free|veteran status|genetic information", re.I)
_JD_REQUIREMENT_RE = re.compile(r"experience|years|skill|required|requirement|must|responsibilit|knowledge|proficien|qualification", re.I)
JD_MAX_CHARS = 9000


def trim_jd(jd_text: str) -> str:
    paras = re.split(r"\n\s*\n", (jd_text or "").strip())
    kept = [p for p in paras if not (_JD_BOILERPLATE_RE.search(p) and not _JD_REQUIREMENT_RE.search(p))]
    out = "\n\n".join(kept).strip() or (jd_text or "").strip()
    return out[:JD_MAX_CHARS]

_BREAKDOWN_CAPS = {
    "mandatory_skills": 40, "recent_project_relevance": 25, "domain_experience": 15,
    "tools_frameworks_cloud": 10, "certifications_education": 5, "location_work_authorization": 5,
}


def decide_enhancement(initial: int):
    """(decision label, enhancement level, highest allowed target %) from the master prompt's
    match bands. Decided here in code from the percentage - never taken from the model - so
    the bands are always applied exactly as written."""
    if initial < 60:
        return "Reject (Profile Gap Too Wide)", "None", initial
    if initial <= 70:
        return "Moderate Enhancement (Candidate Viable for C2C Submission)", "Moderate", 90
    if initial <= 85:
        return "Strong ATS Optimization (High Placement Probability)", "Strong", 96
    if initial <= 90:
        return "Minimal Enhancement / Fine-Tuning", "Minimal", 95
    return "No Major Changes Needed (Already a Strong Match)", "None", initial


_SECTION_WORDS = ("summary", "profile", "experience", "skills", "resume", "objective", "education", "curriculum", "contact")


def detect_candidate_name(resume_text: str):
    """(name, detected). detected=False means the first line doesn't look like a person's name
    (e.g. it is a section heading), so callers should use the selected consultant's name."""
    lines = [l.strip() for l in (resume_text or "").split("\n") if l.strip()]
    if not lines:
        return "", False
    first = lines[0]
    looks_like_name = (
        len(first) <= 40 and "@" not in first and ":" not in first
        and not re.search(r"\d", first) and 1 <= len(first.split()) <= 5
        and re.search(r"[A-Za-z]", first)
        and not any(w in first.lower() for w in _SECTION_WORDS)
    )
    return (first, True) if looks_like_name else ("Technical Consultant", False)


_YEAR_RE = re.compile(r"\b(?:19[7-9]\d|20[0-3]\d)\b")      # same rule as docx_editor: 1970-2039
_YEARS_EXP_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", re.I)


def check_resume_safety(original: str, updated: str) -> str:
    """The master prompt's NEVER rules that can be verified mechanically. Returns '' when the
    rewrite is acceptable, otherwise the reason it must be discarded."""
    o_years, n_years = set(_YEAR_RE.findall(original)), set(_YEAR_RE.findall(updated))
    if o_years != n_years:
        return "it changed employment/education dates (" + ", ".join(sorted(o_years ^ n_years)) + ")"
    added = set(_YEARS_EXP_RE.findall(updated)) - set(_YEARS_EXP_RE.findall(original))
    if added:
        return "it introduced new years-of-experience claims (" + ", ".join(sorted(added)) + " years)"
    if len(updated.strip()) < 0.6 * len(original.strip()):
        return "it returned a much shorter resume than the original (likely cut off)"
    return ""


def _parse_json_object(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None
    return None


def _as_list(value):
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _skill_plan(data: dict) -> dict:
    sa = data.get("skills_to_add") if isinstance(data.get("skills_to_add"), dict) else {}
    return {
        "summary": _as_list(sa.get("summary")),
        "technical_skills": _as_list(sa.get("technical_skills")),
        "projects": _as_list(sa.get("projects") or sa.get("recent_projects")),
        "environment": _as_list(sa.get("environment")),
    }


def _analysis_result(data: dict, resume_text: str):
    """Step 1: Gemini's analysis JSON -> the app's response shape, with the master prompt's rules
    applied in code. Returns (result, plan) or (None, reason) if the answer is unusable. The
    result starts as 'resume unchanged'; step 2 fills in the rewrite."""
    breakdown_raw = data.get("match_breakdown") if isinstance(data.get("match_breakdown"), dict) else {}
    parts = {}
    for key, cap in _BREAKDOWN_CAPS.items():
        n = _num(breakdown_raw.get(key))
        if n is not None:
            parts[key] = max(0.0, min(float(cap), n))
    if len(parts) == len(_BREAKDOWN_CAPS):
        initial = int(round(sum(parts.values())))       # the weighted parts decide, so the % can't be inflated on its own
    else:
        n = _num(data.get("initial_match_percentage"))
        if n is None:
            return None, "Gemini's answer had no match percentage"
        initial = int(round(max(0.0, min(100.0, n))))
        parts = {}

    decision, level, target_cap = decide_enhancement(initial)
    tgt = _num(data.get("target_match_percentage"))
    target = initial if target_cap <= initial else int(round(max(initial, min(target_cap, tgt if tgt is not None else target_cap))))

    name, detected = detect_candidate_name(resume_text)
    return {
        "candidate_name": name,
        "candidate_name_detected": detected,
        "initial_match_percentage": initial,
        "target_match_percentage": target,
        "match_breakdown": parts,
        "match_decision": decision,
        "enhancement_level": level,
        "analysis_source": "gemini",
        "ai_powered": True,
        "optimized": False,
        "not_optimized_reason": "",
        "ai_unavailable_reason": "",
        "domain_detected": str(data.get("domain_detected") or "Not detected").strip(),
        "mandatory_matched_skills": _as_list(data.get("strong_match_skills")),
        "partial_match_skills": _as_list(data.get("partial_match_skills")),
        "mandatory_missing_skills": _as_list(data.get("mandatory_missing_skills")),
        "preferred_missing_skills": _as_list(data.get("preferred_missing_skills")),
        "risky_skills_avoided": _as_list(data.get("risky_skills_avoided")),
        "skills_added": {"summary": [], "technical_skills": [], "recent_projects": [], "environment": []},
        "ats_optimization_notes": _as_list(data.get("ats_optimization_notes")),
        "updated_resume_text": resume_text,
        "format_preserved": False,
        "docx_base64": "",
        "changes": [],
        "skipped_edits": [],
        "ai_model": "",
    }, _skill_plan(data)


def _tech_present(term: str, text: str) -> bool:
    # '-' is a separator here ("AWS-hosted", "Java-based" do mention AWS / Java), unlike '+', '#', '.'
    return re.search(r"(?<![\w.+#])" + re.escape(term) + r"(?![\w+#])", text or "", re.I) is not None


def _unapproved_tech(original_text: str, new_text: str, plan: dict):
    """Known technologies (TECH_KEYWORDS) that `new_text` mentions, that the ORIGINAL resume does
    not, and that step 1 did not approve in skills_to_add. This is what stops the writer from
    adding a technology the analysis classified as risky or never approved."""
    approved = [s.lower() for skills in plan.values() for s in skills if len(s) >= 2]
    bad = []
    for term in TECH_KEYWORDS:
        if _tech_present(term, new_text) and not _tech_present(term, original_text):
            t = term.lower()
            if not any(t in a or (len(a) >= 3 and a in t) for a in approved):
                bad.append(term)
    return bad


_SECTION_HINTS = (
    ("summary", ("summary", "profile", "objective")),
    ("technical_skills", ("skill", "technolog", "competenc", "tools")),
    ("projects", ("experience", "project", "employment", "history")),
)
_ENV_LINE_RE = re.compile(r"^\s*(?:environment|tools?(?: used)?|technologies(?: used)?)\s*[:\-]", re.I)


def _section_of_heading(line: str):
    low = line.lower()
    for key, hints in _SECTION_HINTS:
        if any(h in low for h in hints):
            return key
    return None


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 45 or line.endswith(".") or line[:1] in "•-*▪●◦–" or _ENV_LINE_RE.match(line):
        return False
    return line.isupper() or line.endswith(":") or (_section_of_heading(line) is not None and len(line.split()) <= 4)


def _added_by_section(original: str, updated: str) -> dict:
    """The new/changed lines of `updated` (compared with `original`), grouped by the resume
    section they sit in: summary / technical_skills / projects (experience bullets) / environment."""
    o = [l.strip() for l in original.splitlines()]
    n = [l.strip() for l in updated.splitlines()]
    changed = set()
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, o, n, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            changed.update(range(j1, j2))
    groups = {"summary": [], "technical_skills": [], "projects": [], "environment": []}
    section = None
    for j, line in enumerate(n):
        if _looks_like_heading(line):
            section = _section_of_heading(line)      # an unrecognised heading (EDUCATION...) ends the previous section
            continue
        if j in changed:
            key = "environment" if _ENV_LINE_RE.match(line) else section
            if key:
                groups[key].append(line)
    return {k: "\n".join(v) for k, v in groups.items()}


def _verified_skills_added(plan: dict, original: str, updated: str) -> dict:
    """The master prompt's 'Skills Added' section, reported from what is really in the final
    resume: each skill the analysis approved is listed under the section whose new/changed lines
    actually contain it (not where the plan said it would go)."""
    approved = []
    for skills in plan.values():
        for s in skills:
            if s.lower() not in [a.lower() for a in approved]:
                approved.append(s)
    groups = _added_by_section(original, updated)
    found = lambda key: [s for s in approved if _tech_present(s, groups[key])]
    return {"summary": found("summary"), "technical_skills": found("technical_skills"),
            "recent_projects": found("projects"), "environment": found("environment")}


def _approved_block(result: dict, plan: dict) -> str:
    def line(label, skills):
        return f"  {label}: {', '.join(skills) if skills else '(none)'}"
    return (
        "APPROVED ANALYSIS (from step 1 - follow it exactly):\n"
        f"Match decision: {result['match_decision']} (initial match {result['initial_match_percentage']}%, aim for about {result['target_match_percentage']}%)\n"
        f"Domain: {result['domain_detected']}\n"
        "Skills you may add (only these):\n"
        + line("Professional Summary", plan["summary"]) + "\n"
        + line("Technical Skills", plan["technical_skills"]) + "\n"
        + line("Recent project bullets", plan["projects"]) + "\n"
        + line("Environment line", plan["environment"]) + "\n"
        f"Skills NOT to add: {', '.join(result['risky_skills_avoided']) or '(none listed)'}\n"
    )


def _strip_fences(text: str) -> str:
    return re.sub(r"^```[a-z]*\s*|\s*```$", "", (text or "").strip(), flags=re.I).strip()


def _finish_rewrite(result, plan, raw, truncated, resume_text, docx_bytes):
    """Step 2: turns the written resume into the final result. Any problem leaves the resume
    unchanged and says why (result['not_optimized_reason'])."""
    problem, new_docx, changes, skipped, updated = "", None, [], [], resume_text

    if docx_bytes is not None:
        _, paragraphs = docx_editor.load_paragraphs(docx_bytes)
        entries = docx_editor.parse_numbered(raw)
        if truncated and entries:
            entries = entries[:-1]              # the last paragraph may have been cut off mid-sentence
        edits, seen = docx_editor.edits_from_rewrite(entries, paragraphs)
        if REWRITE_MODE == "full" and seen < max(1, int(0.8 * len(paragraphs))):
            problem = (f"the rewritten resume came back incomplete ({seen} of {len(paragraphs)} paragraphs"
                       f"{' - it hit the length limit' if truncated else ''})")
        elif not edits:
            problem = "the AI returned the resume without changing anything"
        else:
            def unapproved(_old, new, _inserting):
                bad = _unapproved_tech(resume_text, new, plan)
                return f"it adds technology the analysis did not approve ({', '.join(bad)})" if bad else ""
            try:
                new_docx, changes, skipped = docx_editor.apply_edits(docx_bytes, edits, extra_check=unapproved)
            except Exception as ex:
                logger.warning(f"Applying docx edits failed: {ex}")
                problem = "the changes could not be applied to your Word file"
            else:
                if not changes:
                    new_docx, problem = None, "none of the AI's changes passed the safety checks (see below)"
                else:
                    updated = docx_editor.extract_text(new_docx)
                    problem = check_resume_safety(resume_text, updated)
                    if problem:
                        new_docx, changes, updated = None, [], resume_text
    else:
        updated = _apply_text_changes(resume_text, _strip_fences(raw), truncated) if REWRITE_MODE == "changes" else _strip_fences(raw)
        if truncated and REWRITE_MODE == "full":
            problem = "the rewritten resume hit the length limit and was cut off"
        elif not updated:
            problem = "the AI returned no resume"
        else:
            problem = check_resume_safety(resume_text, updated)
            if not problem:
                bad = _unapproved_tech(resume_text, updated, plan)
                if bad:
                    problem = f"it added technology the analysis did not approve ({', '.join(bad)})"
        if problem or updated.strip() == resume_text.strip():
            if not problem:
                problem = "the AI returned the resume without changing anything"
            updated = resume_text

    if problem:
        result["not_optimized_reason"] = f"Your resume was left unchanged because {problem}. Click Optimize again to retry."
        result["skipped_edits"] = skipped
        return result

    result.update({
        "optimized": True,
        "updated_resume_text": updated,
        "skills_added": _verified_skills_added(plan, resume_text, updated),
        "format_preserved": new_docx is not None,
        "docx_base64": base64.b64encode(new_docx).decode("ascii") if new_docx is not None else "",
        "changes": changes,
        "skipped_edits": skipped,
    })
    return result


def _openrouter_or_xai_generate(system: str, prompt: str, want_json: bool, max_tokens: int):
    """Tries the free OpenRouter backup first, then the paid xAI one, in that order (never both -
    the first one that answers wins). Raises the LAST error if every configured backup failed, or
    if neither is configured (nothing to try)."""
    last_error = None
    if openrouter_configured():
        try:
            return _generate_openrouter(system, prompt, want_json, max_tokens)
        except Exception as ex:
            last_error = ex
            logger.warning(f"OpenRouter backup failed: {ex}")
    if xai_configured():
        try:
            return _generate_grok(system, prompt, want_json, max_tokens)
        except Exception as ex:
            last_error = ex
            logger.warning(f"Grok backup also failed: {ex}")
    if last_error is not None:
        raise last_error
    raise RuntimeError("no backup provider is configured")


def _generate_openrouter(system: str, prompt: str, want_json: bool, max_tokens: int):
    """One call to OpenRouter's free tier (an OpenAI-compatible router in front of many models;
    OPENROUTER_MODEL, default a ':free' Llama variant, picks which one). Free models are capped at
    50 requests/day (1000/day once $10 has ever been added) and 20/minute - a backup, only used
    when Gemini has already failed, should rarely get near either. Raises on failure."""
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        OPENROUTER_API_URL,
        headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY.strip()}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter call failed: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    truncated = choice.get("finish_reason") in ("length", "content_filter")
    return text.strip(), truncated, f"{OPENROUTER_MODEL} (free backup)"


def _generate_grok(system: str, prompt: str, want_json: bool, max_tokens: int):
    """One call to xAI's Grok, via its OpenAI-compatible /v1/chat/completions endpoint (that
    endpoint is marked legacy by xAI but is documented as currently supported; it maps onto this
    app's (text, truncated, model) shape far more directly than their newer Responses API).
    Raises on failure. Called ONLY from _generate below, as the last resort after Gemini's whole
    free-tier chain has failed - never on an ordinary request."""
    payload = {
        "model": XAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if want_json:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        XAI_API_URL,
        headers={"Authorization": f"Bearer {config.XAI_API_KEY.strip()}", "Content-Type": "application/json"},
        json=payload, timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"xAI Grok call failed: HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    truncated = choice.get("finish_reason") == "length"
    return text.strip(), truncated, f"{XAI_MODEL} (backup)"


class ClaudeError(RuntimeError):
    """A failed Claude call (kept apart so its reason isn't reported as a Google one)."""


_CLAUDE_CLIENT = None


def _claude_client():
    """One Anthropic client per server process (reused connection). No SDK retries: if Claude fails the
    next AI in the chain answers instead, which is faster than waiting on retries."""
    global _CLAUDE_CLIENT
    import anthropic
    key = config.ANTHROPIC_API_KEY.strip()
    if _CLAUDE_CLIENT is None or _CLAUDE_CLIENT[0] != key:
        _CLAUDE_CLIENT = (key, anthropic.Anthropic(api_key=key, timeout=CLAUDE_TIMEOUT_SECONDS, max_retries=0))
    return _CLAUDE_CLIENT[1]


def _generate_claude(system: str, prompt: str, want_json: bool, max_tokens: int):
    """One Claude request. Returns (text, truncated, model_used); raises ClaudeError on any failure.
    The system prompt (master prompt + output contract) is the same for every resume, so it is marked
    for prompt caching. Thinking counts against max_tokens, so extra room is added on top of what the
    answer itself needs (capped so a non-streamed request stays well inside the SDK's limits)."""
    try:
        params = dict(
            model=CLAUDE_MODEL,
            max_tokens=min(max_tokens + 8000, 21000),
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt + ("\n\nReturn ONLY the JSON object - no other text." if want_json else "")}],
            output_config={"effort": CLAUDE_EFFORT},
        )
        if CLAUDE_MODEL in _CLAUDE_FALLBACK_MODELS:
            params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            response = _claude_client().beta.messages.create(**params)
        else:
            response = _claude_client().messages.create(**params)
    except Exception as ex:
        raise ClaudeError(f"{ex.__class__.__name__}: {ex}") from ex
    if response.stop_reason == "refusal":
        raise ClaudeError("Claude declined this request (refusal)")
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise ClaudeError("Claude returned an empty answer")
    return text, response.stop_reason == "max_tokens", getattr(response, "model", None) or CLAUDE_MODEL


def _generate(client, types, system: str, prompt: str, want_json: bool, max_tokens: int):
    """Claude first when ANTHROPIC_API_KEY is set; if it fails (or isn't set), the Gemini chain below,
    then the optional OpenRouter / Grok backups. `client` is None when no Gemini key is configured."""
    claude_error = None
    if claude_configured():
        try:
            return _generate_claude(system, prompt, want_json, max_tokens)
        except ClaudeError as ex:
            claude_error = ex
            logger.warning(f"Claude failed, trying the backups: {_explain_gemini_error(ex)}")
    if client is None:
        if openrouter_configured() or xai_configured():
            try:
                return _openrouter_or_xai_generate(system, prompt, want_json, max_tokens)
            except Exception as backup_ex:
                logger.warning(f"Every backup also failed: {backup_ex}")
        raise claude_error or RuntimeError("no AI provider is configured")
    try:
        return _generate_gemini(client, types, system, prompt, want_json, max_tokens)
    except Exception:
        if claude_error is not None:
            raise claude_error              # report the MAIN AI's reason, not a backup's
        raise


def _generate_gemini(client, types, system: str, prompt: str, want_json: bool, max_tokens: int):
    """One Gemini request, made resilient: a 503 (overloaded) is retried once on the same model,
    then - like a 429 (quota) or 404 (model not on this key) - the next model in the chain is tried.
    A bad API key or a blocked prompt fails the same on every model, so it raises straight away.
    Returns (text, truncated, model_used); raises the FIRST error if every model failed - unless an
    xAI (Grok) key is configured, in which case that is tried once as a paid backup first."""
    kwargs = {"response_mime_type": "application/json"} if want_json else {}
    gen_config = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens, temperature=0.3, **kwargs)
    chain = _model_chain()
    started = time.monotonic()
    first_error = None
    timed_out = False
    for model in chain:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model, contents=prompt, config=gen_config)
                text = (response.text or "").strip()
                truncated = False
                try:
                    truncated = "MAX_TOKENS" in str(response.candidates[0].finish_reason).upper()
                except Exception:
                    pass
                return text, truncated, model
            except Exception as ex:
                first_error = first_error or ex
                if _is_timeout_error(ex):
                    # Gemini is answering too slowly: its other free models usually are too, so go
                    # straight to the configured backup instead of waiting again (it used to give up
                    # here without trying the backup at all).
                    timed_out = True
                    break
                transient = _is_transient_gemini_error(ex)
                if not (transient or _is_quota_error(ex) or _is_model_missing_error(ex)) and model == chain[0]:
                    raise
                if transient and attempt == 0 and time.monotonic() - started < 60:
                    time.sleep(3)
                    continue
                break                                   # give up on this model, try the next one
        if timed_out or time.monotonic() - started > 75:
            break                                       # don't let one step run past the server's time limit
    # Gemini's whole free-tier chain failed. Try the optional backups, if configured, before
    # giving up - this is the ONLY place either is ever called, so a day where Gemini works fine
    # never touches them.
    if openrouter_configured() or xai_configured():
        try:
            return _openrouter_or_xai_generate(system, prompt, want_json, max_tokens)
        except Exception as backup_ex:
            logger.warning(f"Every backup also failed: {backup_ex}")
    raise first_error


_GEMINI_CLIENT = None


def _gemini_client():
    """(client, types) - created once per server process and reused. Building a client loads the SSL
    certificate store (~0.4 s) and a new client also means a new HTTPS connection for every call;
    reusing it keeps the connection open between step 1 and step 2."""
    global _GEMINI_CLIENT
    from google import genai
    from google.genai import types
    key = config.GEMINI_API_KEY.strip()
    if _GEMINI_CLIENT is None or _GEMINI_CLIENT[0] != key:
        _GEMINI_CLIENT = (key, genai.Client(api_key=key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS)), types)
    return _GEMINI_CLIENT[1], _GEMINI_CLIENT[2]


def prewarm():
    """Import the Gemini library ahead of the first optimization (it takes ~1.3 s to import)."""
    if gemini_configured():
        try:
            _gemini_client()
        except Exception as ex:
            logger.warning(f"Gemini prewarm skipped: {ex}")


def _ai_optimize(resume_text: str, jd_text: str, custom_instructions: str, docx_bytes: bytes = None, timings: dict = None,
                 analysis_only: bool = False):
    """Runs the master prompt through Gemini in two steps (analysis, then the complete rewritten
    resume). Returns (result, reason): result is the app's response dict, or None - never an
    exception - when AI is unavailable, with `reason` saying why (no key, free quota used up,
    malformed answer, ...). If step 1 works but step 2 fails, the analysis is still returned with
    the resume unchanged. With `docx_bytes` the rewrite is written back into that original file."""
    if not ai_configured():
        return None, "no ANTHROPIC_API_KEY or GEMINI_API_KEY is configured on the server"
    master = load_master_prompt()
    if not master:
        return None, "MASTER_RESUME_PROMPT.md is missing on the server"

    custom = custom_instructions or "None"
    timings = timings if timings is not None else {}
    jd_text = trim_jd(jd_text)
    cache_key = _analysis_cache_key(master, jd_text, resume_text, custom)
    try:
        t0 = time.monotonic()
        client, types = _gemini_client() if gemini_configured() else (None, None)
        timings["client_s"] = round(time.monotonic() - t0, 2)
        t0 = time.monotonic()
        cached = _cached_analysis(cache_key)
        if cached:
            raw1, model1 = cached
            timings["analysis_cached"] = True
        else:
            raw1, _, model1 = _generate(
                client, types, master + "\n\n" + _ANALYSIS_CONTRACT,
                f"JOB DESCRIPTION:\n{jd_text}\n\nRESUME:\n{resume_text}\n\nOPTIONAL INSTRUCTIONS:\n{custom}\n",
                want_json=True, max_tokens=8192)
            timings["analysis_cached"] = False
        timings["analysis_s"] = round(time.monotonic() - t0, 2)
    except Exception as ex:
        reason = _explain_gemini_error(ex)
        logger.warning(f"AI analysis unavailable: {reason}")
        return None, reason

    if not raw1:
        return None, "The AI returned an empty response (it may have blocked the content)"
    data = _parse_json_object(raw1)
    if data is None:
        return None, "The AI's answer was not valid JSON (it may have been cut off) - try again"
    if not timings.get("analysis_cached"):
        _store_analysis(cache_key, raw1, model1)
    result, plan = _analysis_result(data, resume_text)
    if result is None:
        return None, plan                       # (None, reason)
    result["ai_model"] = model1
    if analysis_only:          # match check (match_check): step 1 only - its answer is cached for the optimizer
        return result, ""

    initial, level = result["initial_match_percentage"], result["enhancement_level"]
    if initial < 60:
        result["not_optimized_reason"] = ("The match is below 60%, so per your master prompt this profile is rejected "
                                          "and the resume was left unchanged.")
        return result, ""
    if level == "None":
        result["not_optimized_reason"] = ("The match is already above 90%, so per your master prompt no major changes are "
                                          "needed - your resume was left unchanged.")
        return result, ""

    # Step 2: the complete updated resume, as plain text.
    try:
        t0 = time.monotonic()
        if REWRITE_MODE == "changes":
            paras = docx_editor.load_paragraphs(docx_bytes)[1] if docx_bytes is not None else _text_paragraphs(resume_text)
            resume_block, contract, max_out = docx_editor.numbered_listing(paras), _REWRITE_CHANGES_CONTRACT, 6144
        else:
            resume_block = docx_editor.numbered_listing(docx_editor.load_paragraphs(docx_bytes)[1]) if docx_bytes is not None else resume_text
            contract, max_out = (_REWRITE_CONTRACT_DOCX if docx_bytes is not None else _REWRITE_CONTRACT_TEXT), 16384
        raw2, truncated, model2 = _generate(
            client, types, master + "\n\n" + contract,
            f"{_approved_block(result, plan)}\nJOB DESCRIPTION:\n{jd_text}\n\nRESUME:\n{resume_block}\n\nOPTIONAL INSTRUCTIONS:\n{custom}\n",
            want_json=False, max_tokens=max_out)
        timings["rewrite_s"] = round(time.monotonic() - t0, 2)
        if model2 != model1:
            result["ai_model"] = f"{model1} (analysis) + {model2} (resume)"
    except Exception as ex:
        reason = _explain_gemini_error(ex)
        logger.warning(f"AI rewrite step failed: {reason}")
        result["not_optimized_reason"] = (f"The match analysis worked, but the step that writes the updated resume failed: "
                                          f"{reason}. Your resume was left unchanged - click Optimize again to retry.")
        return result, ""
    t0 = time.monotonic()
    out = _finish_rewrite(result, plan, raw2, truncated, resume_text, docx_bytes)
    timings["apply_s"] = round(time.monotonic() - t0, 2)
    return out, ""


def _cert_edu_points(text: str) -> int:
    return 5 if re.search(r"\b(bachelor|master|b\.?tech|m\.?tech|b\.?e\b|m\.?s\b|mba|degree|certified|certification|certificate)\b", text, re.I) else 0


def _work_auth_points(text: str) -> int:
    return 5 if re.search(r"\b(u\.?s\.? citizen|green card|gc ead|h-?1b|h4 ?ead|opt|cpt|work authori[sz]ation|authori[sz]ed to work|visa)\b", text, re.I) else 0


def _keyword_analysis(resume_text: str, jd_text: str, ai_reason: str):
    """Analysis-only fallback used when Gemini is unavailable. It measures how many of ~70
    well-known technologies from the JD appear in the resume - a real, verifiable number - but
    it does NOT rewrite the resume: deciding which missing skills a candidate can truthfully
    claim is exactly the judgement the master prompt reserves for a careful reader, and
    inserting them mechanically would fabricate experience."""
    jd_skills = extract_skills_from_text(jd_text)
    resume_skills = extract_skills_from_text(resume_text)
    jd_domain = detect_domain(jd_text)
    resume_lower = {s.lower() for s in resume_skills}
    matched = [s for s in jd_skills if s.lower() in resume_lower]
    missing = [s for s in jd_skills if s.lower() not in resume_lower]
    ratio = (len(matched) / len(jd_skills)) if jd_skills else 0.0
    domain_matched = 1.0 if any(kw in resume_text.lower() for kw in DOMAINS.get(jd_domain, [])) else 0.0

    # Same weights as the master prompt. Parts a keyword scan cannot see (recent-project
    # relevance beyond keywords) reuse the skill ratio; certs/education and work authorization
    # only score when the resume actually mentions them - no free points.
    initial = int(round(
        ratio * 40 + ratio * 25 + domain_matched * 15
        + min(1.0, len(resume_skills) / 8.0) * 10
        + _cert_edu_points(resume_text) + _work_auth_points(resume_text)
    ))
    initial = max(0, min(100, initial))
    # A percentage built from one or two recognised technologies means nothing (a JD about n8n / Claude
    # automation may name a single item this list knows) - so below MIN_JD_TECH the number is withheld.
    scored = len(jd_skills) >= MIN_JD_TECH
    name, detected = detect_candidate_name(resume_text)
    retry_hint = (" Nothing is wrong with your resume or the JD - click Optimize again in a minute or two."
                  if "overloaded" in (ai_reason or "").lower() else "")
    return {
        "candidate_name": name,
        "candidate_name_detected": detected,
        "initial_match_percentage": initial if scored else None,
        "target_match_percentage": initial if scored else None,
        "match_breakdown": {},
        # Never a "Reject" / band decision here: that is a judgement only the AI analysis is allowed to make.
        "match_decision": "AI analysis unavailable - keyword check only (no match decision was made)",
        "enhancement_level": "None",
        "analysis_source": "keyword-scan",
        "ai_powered": False,
        "optimized": False,
        "not_optimized_reason": ("The resume was not changed. Without the AI, the app can only report the keyword gap - "
                                 "it can't tell which missing skills this candidate can truthfully claim, and inserting them "
                                 "mechanically would invent experience." + retry_hint),
        "ai_unavailable_reason": ai_reason,
        "domain_detected": jd_domain,
        "mandatory_matched_skills": matched,
        "partial_match_skills": [],
        "mandatory_missing_skills": missing,
        "preferred_missing_skills": [],
        "risky_skills_avoided": [],
        "skills_added": {"summary": [], "technical_skills": [], "recent_projects": [], "environment": []},
        "ats_optimization_notes": [
            f"Keyword scan only: compared the JD against ~{len(TECH_KEYWORDS)} well-known technologies, so skills outside that list are not counted.",
            f"Found {len(matched)} of {len(jd_skills)} recognised JD technologies in the resume.",
        ] + ([] if scored else [
            f"This JD names only {len(jd_skills)} technolog{'y' if len(jd_skills) == 1 else 'ies'} the scan recognises, so no percentage is shown - "
            f"a number built from so little would be meaningless. The AI analysis reads the whole JD."]),
        "updated_resume_text": resume_text,
    }


def match_check(resume_text, jd_text, docx_bytes=None):
    """Match % only (step 1 of the optimizer), prepared EXACTLY like optimize_resume_for_jd so its
    analysis is cached and an Optimize right after reuses it instead of paying for it twice.
    {match, decision, matched, missing, model} or {error}."""
    jd_text = (jd_text or "").strip()
    if docx_bytes is not None:
        try:
            resume_text = docx_editor.extract_text(docx_bytes)
        except Exception:
            pass
    resume_text = (resume_text or "").strip()
    if not resume_text or not jd_text:
        return {"error": "Both resume and job description are required."}
    jd_info = cloud_align.jd_cloud(jd_text)
    block = cloud_align.prompt_block(jd_info, cloud_align.clouds_in(resume_text))
    result, reason = _ai_optimize(resume_text, jd_text, block, None, {}, analysis_only=True)
    if not result:
        return {"error": reason or "The AI analysis is unavailable."}
    return {"match": result.get("initial_match_percentage"), "decision": result.get("match_decision") or "",
            "matched": result.get("mandatory_matched_skills") or [], "missing": result.get("mandatory_missing_skills") or [],
            "model": result.get("ai_model") or ""}


def optimize_resume_for_jd(resume_text, jd_text, custom_instructions="", docx_bytes=None):
    """Master Resume Optimization & JD Alignment (see MASTER_RESUME_PROMPT.md).

    With Gemini available, the master prompt drives the full analysis + rewrite; the match band
    is applied in code and mechanically checkable safety rules (dates, years of experience) are
    enforced on the result. Without Gemini it returns the keyword analysis only and leaves the
    resume unchanged.

    `docx_bytes` (the original Word file) switches to in-place editing: the result then carries
    `docx_base64`, that same file with only the AI's targeted edits applied, so the original
    formatting is preserved. If the file can't be read, it falls back to the plain-text path."""
    jd_text = (jd_text or "").strip()
    started = time.monotonic()
    timings = {"rewrite_mode": REWRITE_MODE}
    if docx_bytes is not None:
        try:
            resume_text = docx_editor.extract_text(docx_bytes)
        except Exception as ex:
            logger.warning(f"Could not read the original .docx, using text instead: {ex}")
            docx_bytes = None
    resume_text = (resume_text or "").strip()
    if not resume_text or not jd_text:
        return {"error": "Both resume and job description are required."}

    timings["read_resume_s"] = round(time.monotonic() - started, 2)
    # Cloud alignment: the JD's primary cloud goes to the AI as a judged priority (never "relabel the
    # projects"), and the final resume is checked for it - see cloud_align.
    jd_info = cloud_align.jd_cloud(jd_text)
    block = cloud_align.prompt_block(jd_info, cloud_align.clouds_in(resume_text))
    instructions = "\n\n".join(x for x in ((custom_instructions or "").strip(), block) if x)
    result, reason = _ai_optimize(resume_text, jd_text, instructions, docx_bytes, timings)
    if result is None:
        result = _keyword_analysis(resume_text, jd_text, reason)
    result["jd_cloud"] = jd_info
    result["cloud_check"] = cloud_align.validate(result.get("updated_resume_text") or resume_text, jd_info["primary"])
    if jd_info.get("any_cloud"):
        shown = cloud_align.clouds_in(result.get("updated_resume_text") or resume_text)
        result["cloud_check"] = {"target": "any", "ok": True, "sections": {}, "resume_clouds": shown, "equivalents": [],
                                 "message": f"The JD accepts any major cloud ({', '.join(jd_info['mentioned'])})"
                                            + (f" - the resume's {', '.join(shown)} experience counts." if shown else ".")}
    timings["total_s"] = round(time.monotonic() - started, 2)
    result["timings"] = timings
    logger.info("Resume optimizer timings: %s", timings)
    result.setdefault("format_preserved", False)
    result.setdefault("docx_base64", "")
    result.setdefault("changes", [])
    result.setdefault("skipped_edits", [])
    result.setdefault("ai_model", "")
    return result

def create_docx_resume(resume_text, candidate_name="Candidate"):
    """Generates a clean, professionally formatted Microsoft Word (.docx) document."""
    doc = docx.Document()

    # Set 0.75 in margins
    sections = doc.sections
    for section in sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(0.75)
        section.right_margin = Inches(0.75)

    lines = [l.strip() for l in resume_text.split("\n") if l.strip()]

    # Heading / Name. The resume's first line is only dropped when it IS the name line being
    # replaced by the heading - dropping it unconditionally deleted real content (e.g. a
    # "Professional Summary" heading) from resumes that don't start with the name.
    if lines:
        name_p = doc.add_paragraph()
        name_run = name_p.add_run(candidate_name)
        name_run.bold = True
        name_run.font.size = Pt(18)
        name_run.font.color.rgb = RGBColor(15, 23, 42)
        name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        name_line, name_found = detect_candidate_name(resume_text)
        if name_found and lines[0].strip().lower() == name_line.strip().lower():
            lines = lines[1:]

    for line in lines:
        # Check if section title
        if line.isupper() and len(line) < 40 or line.endswith(":") and len(line) < 35:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(3)
            run = p.add_run(line)
            run.bold = True
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(99, 102, 241)
        elif line.startswith("•") or line.startswith("-") or line.startswith("*"):
            p = doc.add_paragraph(line.lstrip("•-* "), style='List Bullet')
            p.paragraph_format.space_after = Pt(2)
        else:
            p = doc.add_paragraph(line)
            p.paragraph_format.space_after = Pt(3)

    byte_io = io.BytesIO()
    doc.save(byte_io)
    byte_io.seek(0)
    return byte_io
