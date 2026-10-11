"""AI chat for the outreach email (the "Recruiter AI Outreach Copilot" window).

The recruiter types an instruction ("shorter", "say he can interview Tuesday 2-5 PM IST", ...);
the AI edits the CURRENT draft - so earlier changes are kept - using only the facts on file for the
consultant and the job. Same AI chain as the Resume Optimizer (Claude first when ANTHROPIC_API_KEY is
set, then Gemini, then the backups).

Code-enforced guard, whatever the AI says: a rate, a years-of-experience figure, a work-authorization
/ C2C term, an email address or a phone number that is NEW in the draft must come from the facts on
file or from the recruiter's own instruction - otherwise the edit is refused and the draft stays as
it was. C2C / US work-authorization wording is never added for a consultant outside the US.
The reply always says what actually changed; "no change" is said as such.
"""
import difflib
import json
import re
from typing import Dict, List

import gmail_multi_manager
import resume_bot

SYSTEM = """You edit a short job-application email written on behalf of an IT consultant.
Apply the recruiter's instruction to the CURRENT email. Keep everything the instruction doesn't ask
to change (earlier edits included). Plain text only - no markdown, no asterisks. Keep the greeting,
the sign-off and the signature lines (name, phone, email) unless told otherwise.

Facts: use ONLY the facts given in FACTS (FACTS.resume is the resume being attached - its projects and
figures are real) and anything the recruiter states in the INSTRUCTION. If the job description asks
applicants to write something (e.g. a note about a challenge they solved), answer it from FACTS.resume. Never
invent a rate, years of experience, visa / work authorization, availability, notice period, location,
employer, certification or skill. If the instruction needs a fact that is not given, do NOT make it
up: leave that part out and ask for it in "needs_info".
If FACTS.us_based is false, never mention C2C, W2, 1099, H1B, green card or US work authorization.

Return ONLY a JSON object:
{"subject": "...", "body": "...", "changes": ["short description of each change you made"],
 "needs_info": ["question for the recruiter, only when a fact is missing"],
 "reply": "one or two friendly sentences to the recruiter about what you did"}"""

_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?k)?(?:\s?(?:/\s?(?:hr|hour)|per hour|an hour))?"
                    r"|\b\d[\d,]*(?:\.\d+)?\s?(?:/\s?hr|/\s?hour|per hour|lpa)\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2})\+?\s*(?:years|yrs)\b", re.I)
_AUTH = re.compile(r"\b(C2C|corp[- ]to[- ]corp|W-?2|1099|H-?1B|H-?4|green card|GC[- ]EAD|OPT|CPT|TN visa|"
                   r"US citizen|authori[sz]ed to work|work authori[sz]ation|sponsorship)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


def facts(cand: Dict, job: Dict) -> Dict:
    us_based = gmail_multi_manager._is_us_based(cand)
    return {
        "consultant_name": cand.get("name") or "",
        "title": cand.get("title") or "",
        "experience_years": cand.get("experience_years") or "",
        "primary_skills": cand.get("primary_skills") or "",
        "location": cand.get("location") or "",
        "country": cand.get("country") or "",
        "us_based": us_based,
        "visa_status": (cand.get("visa_status") or "").strip() if us_based else "",
        "target_rate": (cand.get("target_rate") or "").strip(),
        "phone": cand.get("phone") or "",
        "email": cand.get("gmail_account") or cand.get("email") or "",
        "job_title": job.get("title") or "",
        "company": job.get("company") or "",
        "job_location": job.get("location") or "",
        "recruiter_name": job.get("recruiter_name") or "",
        "job_description": (job.get("description") or "")[:2500],
    }


def _amount(money: str) -> str:
    return re.sub(r"[^\d.]", "", re.split(r"/|per|an hour|lpa", money, flags=re.I)[0]).rstrip(".")


def guard(f: Dict, old_text: str, new_text: str, instruction: str) -> List[str]:
    """Things the edit ADDS that aren't on file or in the instruction - each is a reason to refuse."""
    allowed = " ".join([old_text, instruction, json.dumps(f, default=str)])
    # a rate in the JOB DESCRIPTION is the client's budget, never the consultant's rate
    own = " ".join([old_text, instruction, json.dumps({k: v for k, v in f.items() if k != "job_description"}, default=str)])
    own_amounts = {_amount(m) for m in _MONEY.findall(own)}
    problems = []
    for m in _MONEY.findall(new_text):
        if _amount(m) not in own_amounts:      # same number = same rate ("$70/hr" on file, "$70 per hour" written)
            problems.append(f"a rate ({m.strip()}) that isn't on file")
    exp = str(f.get("experience_years") or "")
    for n in _YEARS.findall(new_text):
        if n != exp and not re.search(rf"\b{n}\+?\s*(?:years|yrs)\b", old_text + " " + instruction, re.I):
            problems.append(f"{n} years of experience (on file: {exp or 'not set'})")
    old_auth = {a.lower() for a in _AUTH.findall(old_text + " " + instruction)}
    for a in {a.lower() for a in _AUTH.findall(new_text)} - old_auth:
        if not f.get("us_based"):
            problems.append(f"US work wording ('{a}') for a consultant outside the US")
        elif a not in (f.get("visa_status") or "").lower():
            problems.append(f"work authorization '{a}' that isn't on file")
    for e in set(_EMAIL.findall(new_text)) - set(_EMAIL.findall(allowed)):
        problems.append(f"an email address ({e}) that isn't on file")
    digits_allowed = re.sub(r"\D", "", allowed)
    for p in _PHONE.findall(new_text):
        d = re.sub(r"\D", "", p)
        if len(d) >= 10 and d not in digits_allowed:
            problems.append(f"a phone number ({p.strip()}) that isn't on file")
    return list(dict.fromkeys(problems))


def changed_lines(old: str, new: str) -> int:
    sm = difflib.SequenceMatcher(None, (old or "").splitlines(), (new or "").splitlines())
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")


def edit_draft(cand: Dict, job: Dict, instruction: str, subject: str, body: str) -> Dict:
    """{subject, body, reply, changes, needs_info, lines_changed, model, applied}. Never raises; when the
    AI can't run or the edit is refused, the draft comes back unchanged and `reply` says why."""
    unchanged = {"subject": subject, "body": body, "changes": [], "needs_info": [], "lines_changed": 0,
                 "model": "", "applied": False}
    if not resume_bot.ai_configured():
        return dict(unchanged, reply="The AI isn't set up on the server (no ANTHROPIC_API_KEY or GEMINI_API_KEY), so the draft was not changed. You can still edit it directly.")
    f = facts(cand, job)
    try:   # the resume that will be attached - so "answer the JD's questions" can use real projects
        import application_brief
        f["resume"] = application_brief.resume_text_for(cand, job.get("id"))[0][:8000]
    except Exception:
        f["resume"] = ""
    prompt = (f"FACTS:\n{json.dumps(f, indent=1, default=str)}\n\nCURRENT SUBJECT:\n{subject}\n\n"
              f"CURRENT EMAIL:\n{body}\n\nINSTRUCTION:\n{instruction}\n")
    try:
        client, types = resume_bot._gemini_client() if resume_bot.gemini_configured() else (None, None)
        raw, _, model = resume_bot._generate(client, types, SYSTEM, prompt, want_json=True, max_tokens=2000)
    except Exception as ex:
        return dict(unchanged, reply=f"The AI couldn't answer ({resume_bot._explain_gemini_error(ex)}), so the draft was not changed.")
    data = resume_bot._parse_json_object(raw) or {}
    new_subject = str(data.get("subject") or subject).strip()
    new_body = str(data.get("body") or "").strip() or body
    needs = [str(x) for x in (data.get("needs_info") or []) if str(x).strip()][:5]
    changes = [str(x) for x in (data.get("changes") or []) if str(x).strip()][:8]
    if not data:
        return dict(unchanged, model=model, reply="The AI's answer couldn't be read, so the draft was not changed - please try again.")
    problems = guard(f, subject + "\n" + body, new_subject + "\n" + new_body, instruction)
    if problems:
        return dict(unchanged, model=model, needs_info=needs,
                    reply="I didn't apply that edit because it would add " + "; ".join(problems)
                          + ". Add the detail to the consultant's profile (or say it in your instruction) and ask again.")
    n = changed_lines(body, new_body) + (1 if new_subject != subject else 0)
    if not n:
        msg = "No change was made to the draft."
        if needs:
            msg += " I need a bit more information first."
        return dict(unchanged, model=model, needs_info=needs, reply=msg)
    return {"subject": new_subject, "body": new_body, "changes": changes, "needs_info": needs, "lines_changed": n,
            "model": model, "applied": True,
            "reply": str(data.get("reply") or "").strip() or f"Updated the draft ({n} line{'s' if n != 1 else ''} changed)."}
