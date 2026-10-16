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
  5. Review first (default): the reply email is written and kept with the row ("Ready to review"); the
     recruiter opens it (Review), checks / edits the resume and the email, and creates the draft
     (/api/outreach/create-draft with requirement_id) - "Re: <their subject>" in the vendor's thread.
     REQUIREMENT_AUTO_DRAFT=1 creates the draft straight away instead (the earlier behaviour).
     Either way the application then appears in the tracker as "Draft created".
Nothing is ever sent automatically. Errors (AI unavailable, Gmail busy) are retried up to 3 times.
"""
import email
import imaplib
import json
import logging
import os
import re
import time
from email.utils import parseaddr
from typing import Dict, List, Optional

import reply_inbox
import resume_bot

logger = logging.getLogger("inbox_requirements")

MIN_MATCH = 60
# Review first: prepare the tailored resume + reply, and let the recruiter create the draft after checking them.
AUTO_DRAFT = os.getenv("REQUIREMENT_AUTO_DRAFT", "").strip() == "1"
SCAN_QUERY = '"category:primary newer_than:2d"'
MAX_PER_SCAN = 30
MAX_ATTEMPTS = 3
STATUS_LABELS = {"review": "Ready to review", "drafted": "Draft created", "drafted_input": "Draft created - needs your input", "low_match": "Low match - no draft", "not_requirement": "Not a requirement",
                 "filtered": "Skipped (not a job email)", "error": "Problem - will retry", "checking": "Checking..."}

_SKIP_SENDER = re.compile(r"(no-?reply|do-?not-?reply|notifications?|mailer-daemon|alerts?|jobalerts?|newsletter|updates?)@", re.I)
_SKIP_DOMAINS = ("linkedin.com", "naukri.com", "naukrigulf.com", "indeed.com", "glassdoor.com", "monster.com", "foundit.in",
                 "google.com", "accounts.google.com", "icims.com", "workday.com", "myworkday.com", "greenhouse.io", "lever.co",
                 "facebookmail.com", "youtube.com", "amazon.com", "paypal.com")
_SKIP_SUBJECT = re.compile(r"\b(verify|verification|otp|password|receipt|invoice|order|payment|transaction|subscription|"
                           r"thank you for applying|application received|your application|security alert|welcome)\b", re.I)

EXTRACT_SYSTEM = """You read one email from an IT consultant's inbox and decide if it is a JOB REQUIREMENT
(a vendor / recruiter / client describing an open position and asking for candidates or resumes).
A recruiter's own message about a specific position IS a requirement even when a job board delivered it
(e.g. a "Dice Private Email" - the job board's wrapper text around it does not change that).
Newsletters, job-board alerts listing jobs, application confirmations, marketing and personal mail are NOT requirements.
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


_REPLY_SUBJECT = re.compile(r"^\s*((re|aw|sv|antw)\s*(\[\d+\])?\s*:\s*)+", re.I)


def is_thread_reply(p: Dict, ours) -> bool:
    """A reply in one of our threads: answers our email (In-Reply-To / References) or its subject is 'Re: ...'."""
    refs = p.get("references_hdr") or ""
    return bool(p.get("in_reply_to") in ours or any(m and m in refs for m in ours) or _REPLY_SUBJECT.match(p.get("subject") or ""))


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


# Job-board relay addresses: a recruiter writing to the consultant THROUGH the job board (Dice Private
# Email comes from <random>@user.dice.com; replying to it reaches the recruiter).
RELAY_DOMAINS = ("user.dice.com",)


RELAY_BOARD_DOMAINS = ("dice.com",)      # addresses of the job board itself - never the recruiter


def is_relay(email_addr: str) -> bool:
    d = (email_addr or "").rsplit("@", 1)[-1].lower()
    return any(d == r or d.endswith("." + r) for r in RELAY_DOMAINS)


def _board_address(email_addr: str) -> bool:
    d = (email_addr or "").rsplit("@", 1)[-1].lower()
    return any(d == r or d.endswith("." + r) for r in RELAY_BOARD_DOMAINS)


def recruiter_address_in(full_text: str) -> str:
    """The recruiter's own address written in a relayed email (usually the signature: 'E: risa@techdigitalcorp.com')."""
    import vendors
    for e in vendors.find_emails(full_text):
        if not _board_address(e):
            return e
    return ""


def extract(p: Dict, full_text: str) -> Dict:
    relay = is_relay(p["from_email"])
    note = ("NOTE: this message was written by a recruiter and delivered through the job board's private messaging "
            "(the sender address is the job board's relay). For recruiter_email give the recruiter's OWN address from "
            "the email (e.g. the signature), not the relay.\n") if relay else ""
    data, model = reply_inbox._ai(EXTRACT_SYSTEM, f"{note}FROM: {p['from_name']} <{p['from_email']}>\nSUBJECT: {p['subject']}\n\nEMAIL:\n{full_text[:12000]}")
    data["_model"] = model
    # a recruiter address only counts if it really appears in the email (or is the sender)
    rec = (data.get("recruiter_email") or "").strip().lower()
    if not rec or (rec not in full_text.lower() and rec != p["from_email"]):
        rec = p.get("reply_to") or p["from_email"]
    # A job-board relay (Dice Private Email): answer the recruiter's own address when the email gives one -
    # the draft then goes straight to them; without one, the relay still forwards it.
    if relay and (is_relay(rec) or _board_address(rec)):
        rec = recruiter_address_in(full_text) or rec
    data["recruiter_email"] = rec
    return data


def scan_candidate(conn, cand: Dict, user_id: Optional[int] = None, imap_factory=None) -> Dict:
    account, password = reply_inbox._creds(cand)
    known = _known(conn, cand["id"])
    stats = {"checked": 0, "review": 0, "drafted": 0, "low_match": 0, "not_requirement": 0, "filtered": 0, "errors": 0, "skipped_known": 0}
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
        if not p["message_id"] or p["from_email"] == account.lower():
            continue
        # A real reply to one of our applications belongs to the Replies feature. The same recruiter writing a
        # NEW email (fresh subject, not in our thread) is often a new requirement - that one is checked here.
        if is_thread_reply(p, known["ours"]) and (p["message_id"] in known["replies"] or p["from_email"] in known["addrs"]
                                                  or p["in_reply_to"] in known["ours"]):
            stats["skipped_known"] += 1
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
        _set(conn, rid, status="not_requirement", ai_model=data.get("_model"),
             reason=f"The AI read it as not a job requirement (it read {len(full_text.strip()):,} characters of the email).")
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
    import consultant_rules
    res = resume_bot.optimize_resume_for_jd(resume_text, jd, custom_instructions=consultant_rules.text_for(cand["id"], "resume"), docx_bytes=docx)
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
        "matched_skills": ", ".join(data.get("skills") or [])[:500], "match_score": match}, match_title_company=False)
    job_id = job["id"] if isinstance(job, dict) else job
    version_id, attachment = None, (name, original)
    tailored = resume_versions.decode_docx(res.get("docx_base64") or "") if res.get("optimized") else None
    if tailored:
        skill = resume_versions.primary_skill(res, cand, jd)
        fname = resume_versions.make_filename(cand.get("name") or "Consultant", skill, resume_versions.taken_names(cand["id"]))
        version_id = resume_versions.save(cand["id"], job_id, fname, tailored, skill, "", jd[:300], user_id or cand.get("assigned_user_id") or 1)
        attachment = (fname, tailored)
    import application_brief
    their_subject = p["subject"] if p["subject"].lower().startswith("re:") else f"Re: {p['subject']}"
    plan = application_brief.build(cand, models.get_job_by_id(job_id) or {}, jd, version_id=version_id, reply=True,
                                   skills=res.get("mandatory_matched_skills") or [], default_to=data["recruiter_email"],
                                   default_subject=their_subject)
    # Answer in their thread - unless the JD says to apply to a different address (then it's a new email).
    thread_emails = [e for e in dict.fromkeys((data["recruiter_email"], p["from_email"], p.get("reply_to") or "")) if e]
    in_thread = plan["to"].lower() in thread_emails
    needs = plan["needs_input"]
    if not AUTO_DRAFT:
        _set(conn, rid, status="review", match_pct=match, job_id=job_id, resume_version_id=version_id, ai_model=data.get("_model"),
             draft_to=plan["to"], draft_subject=plan["subject"], draft_body=plan["body"],
             draft_checklist=json.dumps(plan.get("checklist") or []), thread_refs=p.get("references_hdr") or "",
             thread_emails=",".join(thread_emails),
             reason=f"Match {match}% - resume " + (f"tailored ({attachment[0]})" if version_id else "unchanged (no changes needed)")
                    + " and reply written. Review them, then create the draft."
                    + (f" Needs your input: {'; '.join(x.strip('[]') for x in needs)}" if needs else ""))
        return "review"
    out = gmail_multi_manager.create_candidate_draft(
        cand["id"], job_id, custom_to_email=plan["to"], custom_subject=plan["subject"], custom_body=plan["body"],
        attachment=attachment, acting_user_id=user_id or cand.get("assigned_user_id"), resume_version_id=version_id,
        reply_to={"message_id": p["message_id"], "references": p.get("references_hdr") or "", "subject": p["subject"]} if in_thread else None)
    if not out.get("success"):
        raise RuntimeError(out.get("error") or "Could not create the Gmail draft")
    _set(conn, rid, status="drafted_input" if needs else "drafted", match_pct=match, job_id=job_id, resume_version_id=version_id,
         ai_model=data.get("_model"),
         reason=f"Match {match}% - draft created with {attachment[0]}" + ("" if version_id else " (original resume - no changes needed)")
                + (f". Needs your input in Gmail: {'; '.join(x.strip('[]') for x in needs)}" if needs else "")
                + (f". Sent to {plan['to']} as the job asks" if not in_thread else ""))
    return "drafted"


RECHECKABLE = ("not_requirement", "filtered", "low_match")


def get(conn, rid: int) -> Optional[Dict]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM inbox_requirements WHERE id = ?", (rid,))
    row = cur.fetchone()
    return dict(row) if row else None


def reply_headers(row: Dict, to_email: str) -> Optional[Dict]:
    """The vendor thread to answer in, when the draft goes to the address that wrote (else a new email)."""
    emails = [e.strip().lower() for e in (row.get("thread_emails") or "").split(",") if e.strip()]
    if (to_email or "").strip().lower() not in emails:
        return None
    return {"message_id": row["message_id"], "references": row.get("thread_refs") or "", "subject": row.get("subject") or ""}


def mark_drafted(conn, rid: int, filename: str, version_id: Optional[int]) -> None:
    _set(conn, rid, status="drafted", resume_version_id=version_id,
         reason=f"Draft created after your review" + (f" with {filename}" if filename else "") + ". Check it in Gmail and press Send.")


def recheck(conn, rid: int) -> Optional[int]:
    """'Check again': a requirement that was skipped / judged not a requirement / low match is looked at
    again on the next scan (as a fresh attempt). Returns its candidate id, or None if it can't be."""
    cur = conn.cursor()
    cur.execute("SELECT candidate_id, status FROM inbox_requirements WHERE id = ?", (rid,))
    row = cur.fetchone()
    if not row or row[1] not in RECHECKABLE:
        return None
    cur.execute("UPDATE inbox_requirements SET status = 'error', attempts = 0, reason = 'Checking again on the next scan' WHERE id = ?", (rid,))
    conn.commit()
    return row[0]


def note_scan_failure(conn, cand_id: int, problem: str) -> None:
    """A scan couldn't open Gmail: emails waiting to be checked again say why (instead of waiting silently)."""
    cur = conn.cursor()
    cur.execute("""UPDATE inbox_requirements SET reason = ? WHERE candidate_id = ? AND status = 'error' AND COALESCE(attempts, 0) = 0""",
                (f"Not checked yet - {problem[:250]}. It will be tried again on the next scan.", cand_id))
    conn.commit()


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
