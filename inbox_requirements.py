"""New requirement emails in a consultant's Gmail -> tailored resume -> Gmail draft reply (never sent).

scan_candidate():
  1. Opens the consultant's Gmail (App Password, INBOX read-only, BODY.PEEK - nothing is marked read)
     and lists recent mail in the Primary tab (Gmail's X-GM-RAW "category:primary newer_than:2d").
  2. Skips what the Replies feature owns (mail from an open application's recruiter, or answering our
     own email) and obvious non-jobs (no-reply senders, LinkedIn / Naukri / job-board notifications,
     receipts, OTPs, "thank you for applying").
  3. Each remaining message is stored once (unique candidate + Message-ID = also the claim between
     server workers). The AI decides whether it is a real job requirement and reads out title,
     client, location, skills, contract type, rate and the recruiter's address.
  4. The Resume Optimizer runs on the consultant's original resume: below MIN_MATCH % nothing is
     drafted (the reason is kept). Otherwise the tailored resume is saved as a version (or the
     original is used when no changes were needed).
  5. A Gmail draft "Re: <their subject>" goes into the vendor's thread with the standard pitch and
     that resume attached; the application appears in the tracker as "Draft created".
Nothing is ever sent automatically. Errors (AI unavailable, Gmail busy) are retried up to 3 times.
"""
import email
import imaplib
import logging
import re
import time
from email.utils import parseaddr
from typing import Dict, List, Optional

import reply_inbox
import resume_bot

logger = logging.getLogger("inbox_requirements")

MIN_MATCH = 60
SCAN_QUERY = '"category:primary newer_than:2d"'
MAX_PER_SCAN = 30
MAX_ATTEMPTS = 3
STATUS_LABELS = {"drafted": "Draft created", "low_match": "Low match - no draft", "not_requirement": "Not a requirement",
                 "filtered": "Skipped (not a job email)", "error": "Problem - will retry", "checking": "Checking..."}

_SKIP_SENDER = re.compile(r"(no-?reply|do-?not-?reply|notifications?|mailer-daemon|alerts?|jobalerts?|newsletter|updates?)@", re.I)
_SKIP_DOMAINS = ("linkedin.com", "naukri.com", "naukrigulf.com", "indeed.com", "glassdoor.com", "monster.com", "foundit.in",
                 "google.com", "accounts.google.com", "icims.com", "workday.com", "myworkday.com", "greenhouse.io", "lever.co",
                 "facebookmail.com", "youtube.com", "amazon.com", "paypal.com")
_SKIP_SUBJECT = re.compile(r"\b(verify|verification|otp|password|receipt|invoice|order|payment|transaction|subscription|"
                           r"thank you for applying|application received|your application|security alert|welcome)\b", re.I)

EXTRACT_SYSTEM = """You read one email from an IT consultant's inbox and decide if it is a JOB REQUIREMENT
(a vendor / recruiter / client describing an open position and asking for candidates or resumes).
Newsletters, job-board alerts, application confirmations, marketing and personal mail are NOT requirements.
Return ONLY JSON:
{"is_requirement": true/false, "title": "", "client": "", "vendor_company": "", "location": "",
 "employment_type": "C2C / W2 / 1099 / Full-time / Contract / unknown", "rate": "only if stated", "skills": ["..."],
 "recruiter_name": "", "recruiter_email": "only an address that appears in the email", "job_description": "the job details, cleaned"}"""


def _skip_reason(p: Dict) -> str:
    sender = p.get("from_email") or ""
    domain = sender.rsplit("@", 1)[-1].lower()
    if _SKIP_SENDER.search(sender):
        return "automatic sender (no-reply / notifications)"
    if any(domain == d or domain.endswith("." + d) for d in _SKIP_DOMAINS):
        return f"job board / service notification ({domain})"
    if _SKIP_SUBJECT.search(p.get("subject") or ""):
        return "receipt / verification / application confirmation"
    return ""


def _known(conn, cand_id: int) -> Dict:
    """Addresses and Message-IDs the Replies feature owns, and messages already handled here."""
    cur = conn.cursor()
    cur.execute("""SELECT LOWER(COALESCE(a.recruiter_email, j.recruiter_email, '')) AS addr, a.outreach_message_id
                   FROM applications a LEFT JOIN jobs j ON j.id = a.job_id WHERE a.candidate_id = ?""", (cand_id,))
    rows = cur.fetchall()
    cur.execute("SELECT message_id, status, attempts FROM inbox_requirements WHERE candidate_id = ?", (cand_id,))
    done = {r["message_id"]: (r["status"], r["attempts"] or 0) for r in cur.fetchall()}
    cur.execute("SELECT message_id FROM application_replies WHERE candidate_id = ?", (cand_id,))
    replies = {r["message_id"] for r in cur.fetchall()}
    return {"addrs": {r["addr"] for r in rows if r["addr"]}, "ours": {r["outreach_message_id"] for r in rows if r["outreach_message_id"]},
            "done": done, "replies": replies}


def _claim(conn, cand_id: int, p: Dict, known: Dict) -> Optional[int]:
    cur = conn.cursor()
    prev = known["done"].get(p["message_id"])
    if prev:
        status, attempts = prev
        if status != "error" or attempts >= MAX_ATTEMPTS:
            return None
        cur.execute("SELECT id FROM inbox_requirements WHERE candidate_id = ? AND message_id = ?", (cand_id, p["message_id"]))
        rid = cur.fetchone()[0]
        cur.execute("UPDATE inbox_requirements SET status = 'checking', attempts = COALESCE(attempts, 0) + 1 WHERE id = ? AND status = 'error'", (rid,))
        conn.commit()
        return rid
    try:
        cur.execute("""INSERT INTO inbox_requirements (candidate_id, message_id, from_email, from_name, subject, received_at, status, attempts)
                       VALUES (?, ?, ?, ?, ?, ?, 'checking', 1)""",
                    (cand_id, p["message_id"], p["from_email"], p["from_name"], p["subject"], p["received_at"]))
        rid = cur.lastrowid
        conn.commit()
    except Exception as ex:
        conn.rollback()
        if "unique" in str(ex).lower() or "duplicate" in str(ex).lower():
            return None
        raise
    if not rid:
        cur.execute("SELECT id FROM inbox_requirements WHERE candidate_id = ? AND message_id = ?", (cand_id, p["message_id"]))
        rid = cur.fetchone()[0]
    return rid


def _set(conn, rid: int, **fields):
    cur = conn.cursor()
    cur.execute(f"UPDATE inbox_requirements SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", list(fields.values()) + [rid])
    conn.commit()


def extract(p: Dict, full_text: str) -> Dict:
    data, model = reply_inbox._ai(EXTRACT_SYSTEM, f"FROM: {p['from_name']} <{p['from_email']}>\nSUBJECT: {p['subject']}\n\nEMAIL:\n{full_text[:12000]}")
    data["_model"] = model
    # a recruiter address only counts if it really appears in the email (or is the sender)
    rec = (data.get("recruiter_email") or "").strip().lower()
    if not rec or (rec not in full_text.lower() and rec != p["from_email"]):
        rec = p.get("reply_to") or p["from_email"]
    data["recruiter_email"] = rec
    return data


def scan_candidate(conn, cand: Dict, user_id: Optional[int] = None, imap_factory=None) -> Dict:
    account, password = reply_inbox._creds(cand)
    known = _known(conn, cand["id"])
    stats = {"checked": 0, "drafted": 0, "low_match": 0, "not_requirement": 0, "filtered": 0, "errors": 0, "skipped_known": 0}
    try:
        mail = (imap_factory or imaplib.IMAP4_SSL)("imap.gmail.com", 993)
        mail.login(account, password)
        mail.select("INBOX", readonly=True)
        try:
            typ, data = mail.search(None, "X-GM-RAW", SCAN_QUERY)
        except Exception:
            typ, data = "NO", None
        if typ != "OK":
            since = reply_inbox._gmail_date(time.strftime("%Y-%m-%d", time.gmtime(time.time() - 2 * 86400)))
            typ, data = mail.search(None, "SINCE", since)
        ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-MAX_PER_SCAN:]
        messages = []
        for mid in reversed(ids):           # newest first
            try:
                typ, msgdata = mail.fetch(mid, "(BODY.PEEK[])")
                raw = next(x[1] for x in msgdata if isinstance(x, tuple))
            except Exception as ex:
                logger.info(f"Could not read message {mid!r}: {ex}")
                continue
            messages.append(raw)
    except Exception as ex:
        raise reply_inbox.at.TrackerError(f"Could not open {account} (read-only): {str(ex)[:120]}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    for raw in messages:
        msg = email.message_from_bytes(raw)
        p = reply_inbox.parse_message(raw)
        p["reply_to"] = parseaddr(reply_inbox._decode(msg.get("Reply-To")))[1].lower()
        if not p["message_id"] or p["message_id"] in known["replies"]:
            continue
        if p["from_email"] in known["addrs"] or p["in_reply_to"] in known["ours"] or p["from_email"] == account.lower():
            stats["skipped_known"] += 1          # a reply to one of our applications - the Replies feature handles it
            continue
        rid = _claim(conn, cand["id"], p, known)
        if not rid:
            continue
        stats["checked"] += 1
        skip = _skip_reason(p)
        if skip:
            _set(conn, rid, status="filtered", reason=skip)
            stats["filtered"] += 1
            continue
        try:
            outcome = _handle(conn, rid, cand, p, reply_inbox._text_of(msg), user_id)
        except Exception as ex:
            logger.warning(f"Requirement {rid} failed: {ex}")
            _set(conn, rid, status="error", reason=str(ex)[:300])
            outcome = "errors"
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats


def _handle(conn, rid: int, cand: Dict, p: Dict, full_text: str, user_id: Optional[int]) -> str:
    import gmail_multi_manager
    import models
    import resume_versions

    if not resume_bot.ai_configured():
        _set(conn, rid, status="error", reason="The AI isn't set up (no ANTHROPIC_API_KEY or GEMINI_API_KEY).")
        return "errors"
    data = extract(p, full_text)
    if not data.get("is_requirement"):
        _set(conn, rid, status="not_requirement", reason="The AI read it as not a job requirement.", ai_model=data.get("_model"))
        return "not_requirement"
    title = (data.get("title") or p["subject"] or "Requirement")[:200]
    company = (data.get("client") or data.get("vendor_company") or p["from_email"].rsplit("@", 1)[-1])[:200]
    jd = (data.get("job_description") or full_text).strip()
    found = models.find_resume_file(cand)
    if not found:
        _set(conn, rid, status="error", reason="No resume on file for this consultant - upload it on the Consultants tab.", attempts=MAX_ATTEMPTS)
        return "errors"
    name, original = found
    docx = original if name.lower().endswith(".docx") else None
    resume_text = resume_bot.extract_text_from_file_bytes(original, name, strict=True)
    res = resume_bot.optimize_resume_for_jd(resume_text, jd, docx_bytes=docx)
    if res.get("error"):
        raise RuntimeError(res["error"])
    if not res.get("ai_powered"):
        raise RuntimeError("AI analysis unavailable: " + (res.get("ai_unavailable_reason") or "unknown"))
    match = res.get("initial_match_percentage")
    if match is None or match < MIN_MATCH:
        _set(conn, rid, status="low_match", match_pct=match, ai_model=data.get("_model"),
             reason=f"Match {match}% is below {MIN_MATCH}% - no draft. {(res.get('match_decision') or '')[:200]}")
        return "low_match"
    job = models.save_or_update_scraped_job({
        "title": title, "company": company, "location": (data.get("location") or "")[:200], "source": "Email requirement",
        "job_type": data.get("employment_type") or "", "salary": data.get("rate") or "", "recruiter_email": data["recruiter_email"],
        "recruiter_name": (data.get("recruiter_name") or p.get("from_name") or "")[:120], "description": jd[:20000],
        "url": f"email:{cand['id']}:{p['message_id']}", "country": cand.get("country") or "United States",
        "matched_skills": ", ".join(data.get("skills") or [])[:500], "match_score": match})
    job_id = job["id"] if isinstance(job, dict) else job
    version_id, attachment = None, (name, original)
    tailored = resume_versions.decode_docx(res.get("docx_base64") or "") if res.get("optimized") else None
    if tailored:
        skill = resume_versions.primary_skill(res, cand, jd)
        fname = resume_versions.make_filename(cand.get("name") or "Consultant", skill, resume_versions.taken_names(cand["id"]))
        version_id = resume_versions.save(cand["id"], job_id, fname, tailored, skill, "", jd[:300], user_id or cand.get("assigned_user_id") or 1)
        attachment = (fname, tailored)
    out = gmail_multi_manager.create_candidate_draft(
        cand["id"], job_id, custom_to_email=data["recruiter_email"], attachment=attachment,
        acting_user_id=user_id or cand.get("assigned_user_id"), resume_version_id=version_id,
        reply_to={"message_id": p["message_id"], "references": p.get("references_hdr") or "", "subject": p["subject"]})
    if not out.get("success"):
        raise RuntimeError(out.get("error") or "Could not create the Gmail draft")
    _set(conn, rid, status="drafted", match_pct=match, job_id=job_id, resume_version_id=version_id, ai_model=data.get("_model"),
         reason=f"Match {match}% - draft created with {attachment[0]}" + ("" if version_id else " (original resume - no changes needed)"))
    return "drafted"


def list_for(conn, candidate_ids: List[int], limit: int = 200) -> List[Dict]:
    if not candidate_ids:
        return []
    cur = conn.cursor()
    cur.execute(f"""SELECT q.*, c.name AS candidate_name, j.title AS job_title, j.company AS job_company, v.filename AS resume_filename
                    FROM inbox_requirements q LEFT JOIN candidates c ON c.id = q.candidate_id LEFT JOIN jobs j ON j.id = q.job_id
                    LEFT JOIN optimized_resumes v ON v.id = q.resume_version_id
                    WHERE q.candidate_id IN ({", ".join(["?"] * len(candidate_ids))})
                    ORDER BY q.received_at DESC, q.id DESC LIMIT ?""", list(candidate_ids) + [limit])
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["status_label"] = STATUS_LABELS.get(d.get("status") or "", d.get("status") or "")
        d["created_at"] = str(d.get("created_at") or "")[:16]
        out.append(d)
    return out


def scan_all(conn, candidates: List[Dict], time_budget_s: float = 600) -> Dict:
    started, totals = time.monotonic(), {"consultants": 0, "drafted": 0, "checked": 0, "errors": []}
    for cand in candidates:
        if not (cand.get("gmail_account") and cand.get("gmail_app_password")):
            continue
        if time.monotonic() - started > time_budget_s:
            break
        try:
            s = scan_candidate(conn, cand)
            totals["consultants"] += 1
            totals["drafted"] += s.get("drafted", 0)
            totals["checked"] += s.get("checked", 0)
        except Exception as ex:
            totals["errors"].append(f"{cand.get('name')}: {str(ex)[:100]}")
    return totals
