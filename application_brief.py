"""Application brief: follow what the job description asks, written from the resume being sent.

1. brief(): the AI reads the JD's APPLICATION INSTRUCTIONS - where to send it, the exact subject line,
   the questions it asks ("a short note about an infrastructure challenge you solved"), the details it
   wants (notice period, CTC, ...), the experience range and the must-have skills. An address or a
   subject line only counts if it really appears in the JD.
2. write(): the AI writes the email from the RESUME TEXT that will be attached (the tailored version
   when there is one): its alignment lines cite what that resume shows, each JD question is answered
   in its own short paragraph (STAR) from a real project in the resume, and any detail not on file is
   left as "[please fill: ...]" - never invented.
3. Code guard on the result: outreach_ai.guard (rates, years, visa, contacts) plus metrics - any figure
   like "40%", "3x", "20 minutes", "50 services" must appear in the resume or the facts. One retry with
   the problems listed; if it still fails, the standard pitch is used with "[please add: ...]" for the
   questions, so nothing made-up ever reaches a draft.
4. checklist(): To / subject as asked, every question answered, missing details, experience range.
"""
import json
import re
from typing import Dict, List, Optional

import gmail_multi_manager
import outreach_ai
import resume_bot

FILL_RE = re.compile(r"\[please (?:fill|add):[^\]]*\]", re.I)
_METRIC = re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|x\b|percent|hours?|hrs?|minutes?|mins?|days?|weeks?|months?|servers?|services?|"
                     r"clusters?|nodes?|users?|applications?|apps?|environments?|pipelines?|microservices?|regions?|accounts?|teams?)", re.I)

BRIEF_SYSTEM = """You read a job description and extract ONLY what it explicitly asks applicants to do.
Return ONLY JSON:
{"apply_to_email": "address the JD says to send applications to, else empty",
 "required_subject": "the exact subject line the JD requires, else empty",
 "questions": ["each thing the JD asks the applicant to write about, e.g. 'a short note about an infrastructure challenge you solved and its impact'"],
 "required_details": ["each detail the JD asks for, e.g. 'notice period', 'current CTC', 'expected CTC', 'portfolio link'"],
 "experience_min": number or null, "experience_max": number or null,
 "must_have_skills": ["the key required skills/tools, max 8"]}
Do not invent instructions the JD does not contain."""

WRITE_SYSTEM = """You write a job application email on behalf of an IT consultant.
Sources you may use: FACTS (profile), RESUME (the exact resume that will be attached) and BRIEF (what the
job asks). Never use anything else; never invent projects, employers, clients, tools, numbers or results.

Structure (plain text, no markdown, no asterisks):
- Greeting (use GREETING exactly), one line of genuine interest in the role at the company.
- 2-3 sentences showing fit: name the job's must-have skills that the RESUME really shows, and the
  resume's own projects / employers where they did that work.
- For EACH item in BRIEF.questions: a short paragraph that starts with a plain label (e.g.
  "Infrastructure challenge I solved:") and answers it in STAR form (situation, task, action, result)
  using one real project from the RESUME. Use a number or metric ONLY if it is written in the RESUME;
  if the resume has no figure for the result, describe the outcome in words and add
  "[please add: measurable impact]".
- For EACH item in BRIEF.required_details: give it from FACTS if present, else write
  "[please fill: <detail>]".
- If FACTS.experience_years is above BRIEF.experience_max, do not change the years; keep the tone
  hands-on and practical.
- If FACTS.us_based is false, never mention C2C, W2, H1B, green card or US work authorization.
- A short closing ("My resume is attached...") and the signature lines from FACTS (name, title, phone, email).
Return ONLY JSON: {"body": "...", "answered": ["each question you answered"], "needs_input": ["each [please ...] you left"]}"""


def _ai(system: str, prompt: str):
    client, types = resume_bot._gemini_client() if resume_bot.gemini_configured() else (None, None)
    raw, _, model = resume_bot._generate(client, types, system, prompt, want_json=True, max_tokens=3000)
    return resume_bot._parse_json_object(raw) or {}, model


def brief(jd_text: str) -> Dict:
    """What the JD asks. {} when the AI can't run (the caller falls back to the standard pitch)."""
    jd = (jd_text or "").strip()
    if not jd or not resume_bot.ai_configured():
        return {}
    try:
        data, model = _ai(BRIEF_SYSTEM, f"JOB DESCRIPTION:\n{jd[:15000]}")
    except Exception:
        return {}
    low = jd.lower()
    to = str(data.get("apply_to_email") or "").strip().lower()
    subj = str(data.get("required_subject") or "").strip().strip('"')
    as_int = lambda v: int(v) if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()) else None  # noqa: E731
    return {
        "apply_to_email": to if to and to in low else "",
        "required_subject": subj if subj and subj.lower() in low else "",
        "questions": [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()][:4],
        "required_details": [str(q).strip() for q in (data.get("required_details") or []) if str(q).strip()][:6],
        "experience_min": as_int(data.get("experience_min")), "experience_max": as_int(data.get("experience_max")),
        "must_have_skills": [str(s).strip() for s in (data.get("must_have_skills") or []) if str(s).strip()][:8],
        "model": model,
    }


def guard_metrics(body: str, resume_text: str, facts: Dict) -> List[str]:
    allowed = re.sub(r"\s+", "", (resume_text + " " + json.dumps(facts, default=str)).lower())
    out = []
    for m in _METRIC.findall(FILL_RE.sub("", body)):
        num = re.match(r"\d+(?:\.\d+)?", m).group(0)
        if re.sub(r"\s+", "", m.lower()) not in allowed and f"{num}%" not in allowed and not re.search(rf"(?<![\d.]){re.escape(num)}(?![\d.])", allowed):
            out.append(f"the figure '{m.strip()}' which isn't in the resume")
    return list(dict.fromkeys(out))


def _greeting(job: Dict) -> str:
    first = gmail_multi_manager.person_name(job.get("recruiter_name") or "")
    return f"Hi {first}," if first else "Hi,"


def _fallback(cand: Dict, job: Dict, b: Dict, reply: bool, tailored: bool, skills: List[str]) -> str:
    body = gmail_multi_manager.generate_consultant_pitch(cand, job, "", {"reply": reply, "skills": skills, "tailored": tailored})
    extra = [f"{q[:1].upper() + q[1:]}:\n[please add: {q}]" for q in b.get("questions") or []]
    extra += [f"{d[:1].upper() + d[1:]}: [please fill: {d}]" for d in b.get("required_details") or []]
    if not extra:
        return body
    head, sep, sign = body.partition("\n\nLooking forward to hearing from you.")
    return head + "\n\n" + "\n\n".join(extra) + sep + sign


def write(cand: Dict, job: Dict, jd_text: str, resume_text: str, b: Dict, reply: bool = False,
          tailored: bool = False, skills: Optional[List[str]] = None) -> Dict:
    """{body, needs_input, answered, model, fallback}. Never raises."""
    f = outreach_ai.facts(cand, job)
    f.pop("job_description", None)
    skills = skills or []
    if not resume_bot.ai_configured() or not resume_text.strip():
        return {"body": _fallback(cand, job, b, reply, tailored, skills), "needs_input": [], "answered": [], "model": "", "fallback": True}
    opening = ("This email ANSWERS the vendor's requirement email - open by thanking them for sharing it."
               if reply else "This is a new application email.")
    prompt = (f"GREETING: {_greeting(job)}\n{opening}\n"
              f"{'The attached resume was tailored to this job - say the resume is updated for this role.' if tailored else ''}\n\n"
              f"FACTS:\n{json.dumps(f, indent=1, default=str)}\n\nBRIEF:\n{json.dumps({k: v for k, v in b.items() if k != 'model'}, indent=1)}\n\n"
              f"JOB DESCRIPTION:\n{(jd_text or '')[:6000]}\n\nRESUME:\n{resume_text[:12000]}\n")
    problems: List[str] = []
    model = ""
    for _ in range(2):
        try:
            data, model = _ai(WRITE_SYSTEM, prompt + (f"\nYOUR LAST DRAFT WAS REJECTED because it added {'; '.join(problems)}. Fix that.\n" if problems else ""))
        except Exception:
            break
        body = str(data.get("body") or "").strip()
        if not body:
            break
        allowed_contacts = resume_text + "\n" + (b.get("apply_to_email") or "") + "\n" + (job.get("recruiter_email") or "")
        problems = outreach_ai.guard(f, allowed_contacts, FILL_RE.sub("", body), "") + guard_metrics(body, resume_text, f)
        if not problems:
            needs = [m.group(0) for m in FILL_RE.finditer(body)]
            return {"body": body, "needs_input": needs, "answered": [str(x) for x in data.get("answered") or []], "model": model, "fallback": False}
    body = _fallback(cand, job, b, reply, tailored, skills)
    return {"body": body, "needs_input": [m.group(0) for m in FILL_RE.finditer(body)], "answered": [], "model": model, "fallback": True,
            "rejected": problems}


def checklist(b: Dict, to: str, subject: str, body: str, cand: Dict) -> List[Dict]:
    items = []
    if b.get("apply_to_email"):
        ok = (to or "").strip().lower() == b["apply_to_email"]
        items.append({"ok": ok, "text": f"Send to {b['apply_to_email']} (as the job asks)" + ("" if ok else f" - currently {to or 'empty'}")})
    if b.get("required_subject"):
        ok = (subject or "").strip().lower() == b["required_subject"].lower()
        items.append({"ok": ok, "text": f"Subject exactly \"{b['required_subject']}\"" + ("" if ok else " - currently different")})
    fills = [m.group(0) for m in FILL_RE.finditer(body or "")]
    for q in b.get("questions") or []:
        items.append({"ok": not fills, "text": f"Answers: {q}" + (" - check the [please ...] parts" if fills else "")})
    for m in fills:
        items.append({"ok": False, "text": f"Needs your input: {m.strip('[]')}"})
    exp = cand.get("experience_years")
    try:
        exp = float(exp) if exp not in (None, "") else None
    except (TypeError, ValueError):
        exp = None
    lo, hi = b.get("experience_min"), b.get("experience_max")
    if exp is not None and hi and exp > hi + 2:
        items.append({"ok": False, "text": f"The job asks {lo or '?'}-{hi} years; {cand.get('name')} has {exp:g}+ - may look overqualified (keep a hands-on angle)"})
    elif exp is not None and lo and exp < lo:
        items.append({"ok": False, "text": f"The job asks at least {lo} years; {cand.get('name')} has {exp:g}"})
    return items


def resume_text_for(cand: Dict, job_id: Optional[int], version_id: Optional[int] = None) -> (str, bool):
    """(text of the resume that will be attached, tailored?) - the chosen / latest saved version for this
    job, else the original resume."""
    import docx_editor
    import models
    import resume_versions
    v = resume_versions.get(version_id) if version_id else (resume_versions.latest_for_job(cand["id"], job_id) if job_id else None)
    if v and v.get("data") and v.get("candidate_id") == cand["id"]:
        try:
            return docx_editor.extract_text(bytes(v["data"])), True
        except Exception:
            pass
    if (cand.get("resume_text") or "").strip():
        return cand["resume_text"], False
    found = models.find_resume_file(cand, persist=False)
    if found:
        return resume_bot.extract_text_from_file_bytes(found[1], found[0], strict=True), False
    return "", False


def build(cand: Dict, job: Dict, jd_text: str, version_id: Optional[int] = None, reply: bool = False,
          skills: Optional[List[str]] = None, default_to: str = "", default_subject: str = "",
          resume_text: Optional[str] = None) -> Dict:
    """Everything a draft needs: {to, subject, body, checklist, needs_input, brief, fallback}.
    `resume_text` = the optimized resume as shown in the Optimize window (not saved yet)."""
    b = brief(jd_text)
    if resume_text and resume_text.strip():
        tailored = True
    else:
        resume_text, tailored = resume_text_for(cand, job.get("id"), version_id)
    w = write(cand, job, jd_text, resume_text, b, reply=reply, tailored=tailored, skills=skills)
    to = b.get("apply_to_email") or default_to or (job.get("recruiter_email") or "")
    subject = b.get("required_subject") or default_subject or gmail_multi_manager.build_subject(cand, job)
    return {"to": to, "subject": subject, "body": w["body"], "needs_input": w["needs_input"], "brief": b,
            "checklist": checklist(b, to, subject, w["body"], cand), "fallback": w["fallback"], "tailored": tailored}
