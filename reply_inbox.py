"""Recruiter replies to applications: read them, sort them with AI, prepare answers, save in-thread drafts.

1. sync_candidate(): opens the consultant's Gmail (App Password, INBOX read-only, BODY.PEEK so nothing
   is marked read) and fetches ONLY mail from the recruiter address of each open application, since
   it was drafted / applied. Each message is stored once per consultant (unique index on
   candidate_id + Message-ID) - the insert is also the claim, so two server workers never handle the
   same reply twice.
2. process_reply(): the AI sorts it (interview / question / needs_resume / rejection / auto_reply /
   other), the application status moves forward (auto-replies change nothing), and - unless the
   owner's mode is "off" - a reply is prepared from facts on file (outreach_ai.guard refuses
   invented rates / years / visa / contacts). Mode "send_safe" sends ONLY "needs resume" answers that
   contain no rate, work-authorization or new facts, with the resume attached; everything else waits
   for a person.
3. save_reply_draft(): the answer goes into Gmail Drafts as "Re: ..." with In-Reply-To / References,
   so it sits in the recruiter's thread and someone presses Send.
"""
import email
import email.header
import html
import imaplib
import json
import logging
import mimetypes
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr, parsedate_to_datetime
from typing import Dict, List, Optional

import application_tracker as at
import outreach_ai
import resume_bot

logger = logging.getLogger("reply_inbox")

CATEGORIES = {"interview": "Interview request", "question": "Asks a question", "needs_resume": "Needs resume / more info",
              "rejection": "Rejection / filled", "auto_reply": "Auto-reply", "other": "Other"}
NEEDS_ANSWER = ("interview", "question", "needs_resume")
MODES = {"off": "Off", "draft": "Prepare reply drafts", "send_safe": "Send safe replies"}
WATCH = ("Drafted", "Applied", "Responded", "Screening", "Submitted", "Interviewing")
MAX_PER_APP = 10
BODY_MAX = 20000

CLASSIFY_SYSTEM = """You sort an email a recruiter sent in reply to a job application from an IT consultant.
Return ONLY JSON: {"category": "...", "reason": "one short sentence"} where category is exactly one of:
interview (asks to schedule / confirms an interview or call), question (asks about rate, visa, location,
availability, RTR, experience or anything else needing an answer), needs_resume (asks for the resume, an
updated resume, or more documents), rejection (not selected, position filled or closed),
auto_reply (out-of-office, automatic acknowledgement, no human content), other (anything else)."""

REPLY_SYSTEM = """You write the consultant's reply to a recruiter's email about a job application.
Use ONLY the facts in FACTS and anything the recruiter-side user says in INSTRUCTION. Never invent or
agree to a rate, years of experience, visa / work authorization, availability, notice period, location
or interview time that is not given. If the recruiter asks for something not in FACTS, do not make it
up: leave it out and list it in "needs_info". If FACTS.us_based is false, never mention C2C, W2, H1B,
green card or US work authorization. Plain text, short and polite, sign with the consultant's name,
phone and email. If CURRENT DRAFT is given, edit it as instructed and keep the rest.
Return ONLY JSON: {"body": "...", "needs_info": ["..."], "reply": "one sentence about what you did"}"""


class ReplyError(ValueError):
    pass


# ---------------------------------------------------------------- reading mail

def _creds(cand: Dict):
    account = (cand.get("gmail_account") or "").strip()
    password = (cand.get("gmail_app_password") or "").replace(" ", "")
    if not (account and password):
        raise at.TrackerError("This consultant has no Gmail connected (App Password) - connect it on the Consultants tab.")
    return account, password


def _gmail_date(d: str) -> str:
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError:
        return (datetime.utcnow() - timedelta(days=60)).strftime("%d-%b-%Y")


def _decode(value) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(value or "")))
    except Exception:
        return str(value or "")


def _text_of(msg) -> str:
    plain, htm = None, None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and htm is None:
            htm = text
    if plain is None and htm is not None:
        t = re.sub(r"(?is)<(script|style).*?</\1>", "", htm)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", t)
        plain = html.unescape(re.sub(r"<[^>]+>", "", t))
    return plain or ""


def strip_quoted(text: str) -> str:
    """The recruiter's own words: drops the quoted earlier email ('On ... wrote:', '>' lines, Outlook headers)."""
    out = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if re.match(r"^On .+wrote:\s*$", s) or re.match(r"^-{2,}\s*Original Message", s, re.I) or (out and re.match(r"^From:\s.+", s)):
            break
        if s.startswith(">"):
            continue
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()[:BODY_MAX]


def parse_message(raw: bytes) -> Dict:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_decode(msg.get("From")))
    try:
        received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        received = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    return {"message_id": (msg.get("Message-ID") or "").strip(), "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
            "references_hdr": " ".join((msg.get("References") or "").split()), "from_email": addr.lower(), "from_name": name,
            "subject": _decode(msg.get("Subject")), "received_at": received, "body": strip_quoted(_text_of(msg))}


def _claim(conn, app_id: int, cand_id: int, p: Dict) -> Optional[int]:
    """Store the message; None when it is already stored (by this or another worker)."""
    cur = conn.cursor()
    try:
        cur.execute("""INSERT INTO application_replies (application_id, candidate_id, message_id, in_reply_to, references_hdr,
                       from_email, from_name, subject, received_at, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (app_id, cand_id, p["message_id"], p["in_reply_to"], p["references_hdr"], p["from_email"], p["from_name"],
                     p["subject"], p["received_at"], p["body"]))
        new_id = cur.lastrowid
        conn.commit()
    except Exception as ex:
        conn.rollback()
        if "unique" in str(ex).lower() or "duplicate" in str(ex).lower():
            return None
        raise
    if not new_id:
        cur.execute("SELECT id FROM application_replies WHERE candidate_id = ? AND message_id = ?", (cand_id, p["message_id"]))
        new_id = cur.fetchone()[0]
    return new_id


def sync_candidate(conn, cand: Dict, user_id: Optional[int], process: bool = True) -> Dict:
    account, password = _creds(cand)
    rows = [r for r in at.list_for(conn, [cand["id"]]) if r["stage"] in WATCH and "@" in (r["recruiter_email"] or "")]
    if not rows:
        return {"checked": 0, "replied": [], "new_replies": 0}
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)     # looked up at call time (tests replace it)
        mail.login(account, password)
        mail.select("INBOX", readonly=True)
    except Exception as ex:
        raise at.TrackerError(f"Could not open {account} (read-only): {str(ex)[:120]}")
    replied, new_ids, unread_hits = [], [], []
    try:
        for r in rows:
            since = _gmail_date(r["applied_date"] or r["drafted_at"] or "")
            addr = r["recruiter_email"].strip().replace('"', "")
            typ, data = mail.search(None, "FROM", f'"{addr}"', "SINCE", since)
            ids = data[0].split()[-MAX_PER_APP:] if typ == "OK" and data and data[0] else []
            if not ids:
                continue
            replied.append({"id": r["id"], "job_title": r["job_title"], "from": addr})
            read_any = False
            for mid in ids:
                try:
                    typ, msgdata = mail.fetch(mid, "(BODY.PEEK[])")       # PEEK: the mail stays unread in Gmail
                    raw = next(x[1] for x in msgdata if isinstance(x, tuple))
                    p = parse_message(raw)
                except Exception as ex:
                    logger.info(f"Could not read message {mid!r} for application {r['id']}: {ex}")
                    continue
                read_any = True
                p["message_id"] = p["message_id"] or f"<no-id-{cand['id']}-{r['id']}-{p['received_at']}>"
                rid = _claim(conn, r["id"], cand["id"], p)
                if rid:
                    new_ids.append(rid)
            if not read_any:
                unread_hits.append(r)
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    cur = conn.cursor()
    for r in unread_hits:     # found but couldn't be read: still counts as a response
        _move(cur, r["id"], r["stage"], "Responded", user_id, f"Reply found in {account} from {r['recruiter_email']}")
    conn.commit()
    if process:
        for rid in new_ids:
            try:
                process_reply(conn, rid)
            except Exception as ex:
                logger.warning(f"Could not process reply {rid}: {ex}")
    return {"checked": len(rows), "replied": replied, "new_replies": len(new_ids)}


def _move(cur, app_id: int, current: str, new: str, user_id, detail: str):
    if new == "Rejected":
        ok = current in at.OPEN
    else:
        ok = at.ORDER.get(new, -1) > at.ORDER.get(current or "Saved", -1)
    if ok:
        cur.execute("UPDATE applications SET stage = ?, last_reply_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new, app_id))
        at.event(cur, app_id, user_id, "status", f"{at.STATUS_LABELS.get(current, current)} -> {at.STATUS_LABELS.get(new, new)} ({detail})")
    else:
        cur.execute("UPDATE applications SET last_reply_at = CURRENT_TIMESTAMP WHERE id = ?", (app_id,))
        at.event(cur, app_id, user_id, "reply", detail)


# ---------------------------------------------------------------- AI

def _ai(system: str, prompt: str):
    """Replies, reply drafts and requirement emails use Gemini first (REPLY_AI_PROVIDER=gemini, the
    default - its free tier keeps this high-volume work at no cost); if Gemini fails, the normal chain
    (Claude, then the backups) answers. REPLY_AI_PROVIDER=claude uses the normal chain directly."""
    client, types = resume_bot._gemini_client() if resume_bot.gemini_configured() else (None, None)
    if client is not None and os.getenv("REPLY_AI_PROVIDER", "gemini").strip().lower() == "gemini":
        try:
            raw, _, model = resume_bot._generate_gemini(client, types, system, prompt, want_json=True, max_tokens=1500)
            return resume_bot._parse_json_object(raw) or {}, model
        except Exception as ex:
            logger.warning(f"Gemini failed for a reply task, using the backup AI: {resume_bot._explain_gemini_error(ex)}")
    raw, _, model = resume_bot._generate(client, types, system, prompt, want_json=True, max_tokens=1500)
    return resume_bot._parse_json_object(raw) or {}, model


def classify(reply: Dict) -> Dict:
    if not resume_bot.ai_configured():
        return {"category": "other", "reason": "AI isn't set up, so this reply wasn't sorted.", "model": ""}
    try:
        data, model = _ai(CLASSIFY_SYSTEM, f"SUBJECT: {reply.get('subject')}\nFROM: {reply.get('from_email')}\n\nEMAIL:\n{(reply.get('body') or '')[:6000]}")
    except Exception as ex:
        return {"category": "other", "reason": f"Couldn't sort it ({resume_bot._explain_gemini_error(ex)}).", "model": ""}
    cat = str(data.get("category") or "").strip().lower()
    return {"category": cat if cat in CATEGORIES else "other", "reason": str(data.get("reason") or "")[:300], "model": model}


def draft_reply(cand: Dict, job: Dict, reply: Dict, instruction: str = "", current: str = "") -> Dict:
    """{body, needs_info, note, applied, model}. A draft that would add facts not on file is refused."""
    if not resume_bot.ai_configured():
        return {"body": current, "needs_info": [], "applied": False, "model": "",
                "note": "The AI isn't set up on the server (no ANTHROPIC_API_KEY or GEMINI_API_KEY) - write the reply yourself."}
    f = outreach_ai.facts(cand, job)
    prompt = (f"FACTS:\n{json.dumps(f, indent=1, default=str)}\n\nRECRUITER'S EMAIL (from {reply.get('from_email')}, subject "
              f"{reply.get('subject')!r}):\n{(reply.get('body') or '')[:6000]}\n\nCURRENT DRAFT:\n{current or '(none)'}\n\n"
              f"INSTRUCTION:\n{instruction or 'Write a short, polite reply that answers what the recruiter asked, using only the facts.'}\n")
    try:
        data, model = _ai(REPLY_SYSTEM, prompt)
    except Exception as ex:
        return {"body": current, "needs_info": [], "applied": False, "model": "",
                "note": f"The AI couldn't answer ({resume_bot._explain_gemini_error(ex)})."}
    body = str(data.get("body") or "").strip()
    needs = [str(x) for x in (data.get("needs_info") or []) if str(x).strip()][:5]
    if not body:
        return {"body": current, "needs_info": needs, "applied": False, "model": model, "note": "The AI's answer couldn't be read - try again."}
    # Allowed sources: the facts, the current draft, the instruction and the recruiter's own address.
    # NOT the recruiter's email text - agreeing to a rate they proposed must come from a person.
    problems = outreach_ai.guard(f, (current or "") + "\n" + (reply.get("from_email") or ""), body, instruction)
    if problems:
        return {"body": current, "needs_info": needs, "applied": False, "model": model,
                "note": "Not drafted because it would add " + "; ".join(problems) + ". Tell the AI the detail (or add it to the profile) and ask again."}
    return {"body": body, "needs_info": needs, "applied": True, "model": model, "note": str(data.get("reply") or "").strip()}


# ---------------------------------------------------------------- processing / settings

def owner_mode(conn, cand: Dict) -> str:
    cur = conn.cursor()
    cur.execute("SELECT auto_reply_mode FROM users WHERE id = ?", (cand.get("assigned_user_id"),))
    row = cur.fetchone()
    mode = (row[0] if row else None) or "draft"
    return mode if mode in MODES else "draft"


def set_mode(conn, user_id: int, mode: str) -> str:
    if mode not in MODES:
        raise ReplyError("Mode must be off, draft or send_safe.")
    cur = conn.cursor()
    cur.execute("UPDATE users SET auto_reply_mode = ? WHERE id = ?", (mode, user_id))
    conn.commit()
    return mode


def get(conn, reply_id: int) -> Optional[Dict]:
    cur = conn.cursor()
    cur.execute("""SELECT r.*, a.job_id, a.stage, a.user_id AS app_user_id, a.resume_version_id, j.title AS job_title, j.company AS job_company,
                          c.name AS candidate_name
                   FROM application_replies r JOIN applications a ON a.id = r.application_id
                   LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN candidates c ON c.id = r.candidate_id WHERE r.id = ?""", (reply_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["category_label"] = CATEGORIES.get(d.get("category") or "", "Not sorted yet")
    return d


def list_replies(conn, candidate_ids: List[int], unhandled_only: bool = False) -> List[Dict]:
    if not candidate_ids:
        return []
    cur = conn.cursor()
    cur.execute(f"""SELECT r.id, r.application_id, r.candidate_id, r.from_email, r.from_name, r.subject, r.received_at, r.category,
                           r.category_reason, r.handled, r.gmail_draft_at, r.sent_at, r.draft_body IS NOT NULL AS has_draft,
                           c.name AS candidate_name, j.title AS job_title, j.company AS job_company
                    FROM application_replies r JOIN applications a ON a.id = r.application_id
                    LEFT JOIN candidates c ON c.id = r.candidate_id LEFT JOIN jobs j ON j.id = a.job_id
                    WHERE r.candidate_id IN ({", ".join(["?"] * len(candidate_ids))}) {"AND COALESCE(r.handled, 0) = 0" if unhandled_only else ""}
                    ORDER BY r.received_at DESC, r.id DESC""", list(candidate_ids))
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["has_draft"] = bool(d.get("has_draft"))
        d["category_label"] = CATEGORIES.get(d.get("category") or "", "Not sorted yet")
        out.append(d)
    return out


def _resume_for(conn, cand: Dict, reply: Dict):
    import models
    import resume_versions
    if reply.get("resume_version_id"):
        v = resume_versions.get(reply["resume_version_id"])
        if v and v.get("data"):
            return v["filename"], v["data"]
    return models.find_resume_file(cand)


def process_reply(conn, reply_id: int) -> Dict:
    import models
    r = get(conn, reply_id)
    if not r:
        raise LookupError("Reply not found")
    cand = models.get_candidate_by_id(r["candidate_id"]) or {}
    job = models.get_job_by_id(r["job_id"]) or {}
    c = classify(r)
    cur = conn.cursor()
    cur.execute("UPDATE application_replies SET category = ?, category_reason = ?, ai_model = ? WHERE id = ?",
                (c["category"], c["reason"], c["model"], reply_id))
    if c["category"] == "auto_reply":
        cur.execute("UPDATE application_replies SET handled = 1 WHERE id = ?", (reply_id,))
    else:
        new_stage = {"interview": "Interviewing", "rejection": "Rejected"}.get(c["category"], "Responded")
        _move(cur, r["application_id"], r["stage"], new_stage, None, f"{CATEGORIES[c['category']]} from {r['from_email']}")
    if c["category"] == "rejection":
        cur.execute("UPDATE application_replies SET handled = 1 WHERE id = ?", (reply_id,))
    conn.commit()
    mode = owner_mode(conn, cand)
    result = {"category": c["category"], "mode": mode, "drafted": False, "sent": False}
    if mode == "off" or c["category"] not in NEEDS_ANSWER:
        return result
    d = draft_reply(cand, job, dict(r, category=c["category"]))
    note = d["note"] + ((" Missing: " + "; ".join(d["needs_info"])) if d["needs_info"] else "")
    cur.execute("UPDATE application_replies SET draft_body = ?, draft_note = ? WHERE id = ?",
                (d["body"] if d["applied"] else None, note.strip(), reply_id))
    conn.commit()
    result["drafted"] = d["applied"]
    safe = (mode == "send_safe" and c["category"] == "needs_resume" and d["applied"] and not d["needs_info"]
            and not outreach_ai._MONEY.search(d["body"]) and not outreach_ai._AUTH.search(d["body"]))
    if safe:
        try:
            send_reply(conn, reply_id, d["body"], attach_resume=True)
            result["sent"] = True
        except Exception as ex:
            logger.warning(f"Safe auto-send failed for reply {reply_id}: {ex}")
            cur.execute("UPDATE application_replies SET draft_note = ? WHERE id = ?", (f"Automatic send failed ({str(ex)[:120]}) - send it yourself.", reply_id))
            conn.commit()
    return result


# ---------------------------------------------------------------- answering in the thread

def _reply_message(cand: Dict, r: Dict, body: str, attachment=None) -> EmailMessage:
    account = (cand.get("gmail_account") or "").strip()
    subject = r.get("subject") or ""
    msg = EmailMessage()
    msg["To"] = r["from_email"]
    msg["From"] = account
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    msg["Message-ID"] = make_msgid(domain=account.rsplit("@", 1)[-1] or "jobhunt.local")
    msg["In-Reply-To"] = r["message_id"]
    msg["References"] = " ".join(x for x in ((r.get("references_hdr") or ""), r["message_id"]) if x).strip()
    msg.set_content(body)
    if attachment:
        name, data = attachment
        mime, _ = mimetypes.guess_type(name)
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def save_reply_draft(conn, reply_id: int, body: str, user: Dict, attach_resume: bool = False) -> Dict:
    import models
    r = get(conn, reply_id)
    if not r:
        raise LookupError("Reply not found")
    body = (body or "").strip()
    if not body:
        raise ReplyError("Write the reply first.")
    cand = models.get_candidate_by_id(r["candidate_id"]) or {}
    account, password = _creds(cand)
    attachment = _resume_for(conn, cand, r) if attach_resume else None
    msg = _reply_message(cand, r, body, attachment)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(account, password)
        mail.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        mail.logout()
    except Exception as ex:
        raise ReplyError(f"Could not save the draft in {account}: {str(ex)[:120]}")
    cur = conn.cursor()
    cur.execute("UPDATE application_replies SET draft_body = ?, gmail_draft_at = CURRENT_TIMESTAMP, handled = 1 WHERE id = ?", (body, reply_id))
    at.event(cur, r["application_id"], user.get("id"), "reply_draft", f"Reply to {r['from_email']} saved as a Gmail draft in the same thread"
             + (f" (attached {attachment[0]})" if attachment else ""))
    conn.commit()
    return {"success": True, "attached": attachment[0] if attachment else ""}


def send_reply(conn, reply_id: int, body: str, attach_resume: bool = False) -> None:
    """Used ONLY by the send_safe automation (needs_resume answers without rates / visa / new facts)."""
    import models
    r = get(conn, reply_id)
    cand = models.get_candidate_by_id(r["candidate_id"]) or {}
    account, password = _creds(cand)
    attachment = _resume_for(conn, cand, r) if attach_resume else None
    msg = _reply_message(cand, r, body, attachment)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(account, password)
        smtp.send_message(msg)
    cur = conn.cursor()
    cur.execute("UPDATE application_replies SET draft_body = ?, sent_at = CURRENT_TIMESTAMP, handled = 1 WHERE id = ?", (body, reply_id))
    at.event(cur, r["application_id"], None, "reply_sent", f"Automatic reply sent to {r['from_email']} (asked for the resume)"
             + (f", attached {attachment[0]}" if attachment else ""))
    conn.commit()


def set_handled(conn, reply_id: int, handled: bool) -> None:
    cur = conn.cursor()
    cur.execute("UPDATE application_replies SET handled = ? WHERE id = ?", (1 if handled else 0, reply_id))
    conn.commit()


# ---------------------------------------------------------------- background check

def check_all(conn, candidates: List[Dict], time_budget_s: float = 150) -> Dict:
    started, totals = time.monotonic(), {"consultants": 0, "new_replies": 0, "errors": []}
    for cand in candidates:
        if not (cand.get("gmail_account") and cand.get("gmail_app_password")):
            continue
        if time.monotonic() - started > time_budget_s:
            totals["partial"] = True
            break
        try:
            out = sync_candidate(conn, cand, None)
            totals["consultants"] += 1
            totals["new_replies"] += out["new_replies"]
        except Exception as ex:
            totals["errors"].append(f"{cand.get('name')}: {str(ex)[:100]}")
    return totals
