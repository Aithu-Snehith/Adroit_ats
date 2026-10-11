import sys
import json
import base64
import re
import mimetypes
import imaplib
import time
from pathlib import Path
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Dict, List, Optional, Any, Tuple

import models
import config

BASE_DIR = Path(config.BASE_DIR)
DATA_DIR = BASE_DIR / "data"
TOKENS_DIR = DATA_DIR / "tokens"
RESUMES_DIR = DATA_DIR / "resumes"
OAUTH_CREDS_FILE = BASE_DIR / "oauth_credentials.json"

TOKENS_DIR.mkdir(parents=True, exist_ok=True)
RESUMES_DIR.mkdir(parents=True, exist_ok=True)

GMAIL_SCOPES = [
    "https://mail.google.com/"
]


def oauth_configured() -> bool:
    """Whether the Google OAuth 'Connect Gmail' button can actually work on this
    server. Used to hide that button (and show only the always-available App
    Password option) instead of letting a recruiter click into a guaranteed error."""
    return OAUTH_CREDS_FILE.exists()

def verify_gmail_app_password(gmail_address: str, app_password: str) -> Tuple[bool, str]:
    """Test login to Gmail via IMAP with App Password."""
    clean_pass = app_password.replace(" ", "").strip()
    clean_user = gmail_address.strip()
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", port=993)
        mail.login(clean_user, clean_pass)
        mail.logout()
        return True, "Login successful"
    except Exception as e:
        return False, str(e)

def disconnect_candidate_gmail(candidate_id: int) -> None:
    """Forget a consultant's Gmail: the App Password, the Gmail address it belonged to, and any
    Google OAuth token file - so a different Gmail can be connected. Drafts already in that Gmail
    stay there (nothing in Gmail is touched)."""
    cand = models.get_candidate_by_id(candidate_id) or {}
    for p in {TOKENS_DIR / f"token_{candidate_id}.json", Path(cand["gmail_token_path"]) if cand.get("gmail_token_path") else None} - {None}:
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    models.update_candidate(candidate_id, gmail_account="", gmail_app_password="", gmail_token_path="")


def is_candidate_connected(candidate_id: int) -> Dict[str, Any]:
    cand = models.get_candidate_by_id(candidate_id)
    if not cand:
        return {"connected": False, "message": "Candidate not found"}

    # 1. Check App Password first
    if cand.get("gmail_app_password") and cand.get("gmail_account"):
        return {
            "connected": True,
            "method": "app_password",
            "email": cand.get("gmail_account")
        }

    # 2. Check OAuth token
    token_path = TOKENS_DIR / f"token_{candidate_id}.json"
    if not token_path.exists() and cand.get("gmail_token_path") and Path(cand["gmail_token_path"]).exists():
        token_path = Path(cand["gmail_token_path"])

    if token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            is_valid = creds.valid or (creds.expired and bool(creds.refresh_token))
            if is_valid:
                return {
                    "connected": True,
                    "method": "oauth",
                    "email": cand.get("gmail_account") or cand.get("email"),
                    "token_path": str(token_path)
                }
        except Exception:
            pass

    return {
        "connected": False,
        "email": cand.get("gmail_account") or cand.get("email")
    }

def get_auth_url(candidate_id: int, redirect_uri: str = "http://localhost:5000/api/auth/google/callback") -> Tuple[Optional[str], Optional[str]]:
    creds_file = OAUTH_CREDS_FILE if OAUTH_CREDS_FILE.exists() else Path(r"F:\AI_Job_Hunter\oauth_credentials.json")
    if not creds_file.exists():
        return None, None

    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_secrets_file(
            str(creds_file),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=str(candidate_id)
        )
        verifier = getattr(flow, "code_verifier", None)
        if verifier:
            verifier_file = TOKENS_DIR / f"verifier_{candidate_id}.txt"
            with open(verifier_file, "w", encoding="utf-8") as vf:
                vf.write(verifier)

        return auth_url, verifier
    except Exception as e:
        print(f"[-] Error generating OAuth URL for candidate {candidate_id}: {e}")
        return None, None

def exchange_code_and_save_token(candidate_id: int, auth_code: str, redirect_uri: str = "http://localhost:5000/api/auth/google/callback", code_verifier: Optional[str] = None) -> Dict[str, Any]:
    creds_file = OAUTH_CREDS_FILE if OAUTH_CREDS_FILE.exists() else Path(r"F:\AI_Job_Hunter\oauth_credentials.json")
    if not creds_file.exists():
        return {"success": False, "error": "OAuth credentials file not found"}

    try:
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build

        flow = Flow.from_client_secrets_file(
            str(creds_file),
            scopes=GMAIL_SCOPES,
            redirect_uri=redirect_uri
        )

        if not code_verifier:
            verifier_file = TOKENS_DIR / f"verifier_{candidate_id}.txt"
            if verifier_file.exists():
                with open(verifier_file, "r", encoding="utf-8") as vf:
                    code_verifier = vf.read().strip()

        if code_verifier:
            flow.code_verifier = code_verifier
            flow.fetch_token(code=auth_code, code_verifier=code_verifier)
        else:
            flow.fetch_token(code=auth_code)

        creds = flow.credentials
        token_path = TOKENS_DIR / f"token_{candidate_id}.json"
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        gmail_address = ""
        try:
            service = build("gmail", "v1", credentials=creds)
            profile = service.users().getProfile(userId="me").execute()
            gmail_address = profile.get("emailAddress", "")
        except Exception:
            pass

        models.update_candidate(
            candidate_id,
            gmail_account=gmail_address if gmail_address else None,
            gmail_token_path=str(token_path)
        )

        return {
            "success": True,
            "candidate_id": candidate_id,
            "gmail_account": gmail_address,
            "token_path": str(token_path)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# Job sources that are real sites a recruiter would recognise ("posted on LinkedIn"); anything else -
# e.g. "Manual Paste" for a pasted requirement - is never named in the email.
_SOURCE_SITES = {"linkedin": "LinkedIn", "dice": "Dice", "indeed": "Indeed", "naukri": "Naukri",
                 "foundit": "Foundit", "monster": "Monster"}
_PLACEHOLDER_COMPANIES = {"", "company", "direct client", "direct client / prime vendor", "confidential"}


def _source_site(source: str) -> str:
    s = (source or "").lower()
    return next((name for key, name in _SOURCE_SITES.items() if key in s), "")


def _article(word: str) -> str:
    return "an" if (word or "").strip()[:1].lower() in "aeiou" else "a"


def _is_us_based(candidate: Dict) -> bool:
    return (candidate.get("country") or "United States").strip().lower() in ("united states", "usa", "us")


def _linkedin_of(candidate: Dict) -> str:
    """A real LinkedIn URL for the consultant, or "" (never a guessed one built from the name)."""
    for key in ("linkedin_url", "profile_url"):
        v = (candidate.get(key) or "").strip()
        if "linkedin.com/in/" in v:
            return v
    m = re.search(r'https?://[^\s,"]*linkedin\.com/in/[^\s,"?#]+', candidate.get("resume_summary") or "")
    return m.group(0) if m else ""


def matching_skills(candidate: Dict, job_text: str, emphasis: str = "", limit: int = 4):
    """The consultant's own skills that the requirement actually mentions (in their order), with
    any skill named in the recruiter's emphasis note first. Only real overlaps - nothing invented."""
    skills = [s.strip() for s in (candidate.get("primary_skills") or "").split(",") if s.strip()]
    text, note = (job_text or "").lower(), (emphasis or "").lower()

    def mentioned(skill, hay):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(skill.lower()) + r"(?![a-z0-9])", hay))

    in_job = [s for s in skills if mentioned(s, text)]
    first = [s for s in in_job if note and mentioned(s, note)]
    return (first + [s for s in in_job if s not in first])[:limit]


_LINK_RE = re.compile(r"(https?://[^\s<]+|[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _linkify(escaped_line: str) -> str:
    """Make URLs and email addresses clickable (input is already HTML-escaped)."""
    def repl(m):
        v = m.group(0)
        href = v if v.startswith("http") else f"mailto:{v}"
        return f'<a href="{href}">{v}</a>'
    return _LINK_RE.sub(repl, escaped_line)


def render_email_html(body_text: str) -> str:
    """The email as a person would type it in Gmail: left-aligned, full width, the reader's normal
    email font, one blank line between paragraphs, a plain signature (one line each). No centred
    column, fixed width, tables or styled blocks - a recruiter should see an ordinary personal email,
    not a newsletter. Same markup Gmail itself writes (div per line, <br> for blank lines), so it
    looks the same in Gmail, Outlook / Microsoft 365 and on phones. Everything is HTML-escaped;
    email addresses and links stay clickable."""
    import html as _html
    text = (body_text or "").replace("\r\n", "\n").strip()
    paragraphs = [p.strip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
    blocks = []
    for para in paragraphs:
        lines = [_linkify(_html.escape(l.rstrip())) for l in para.split("\n")]
        blocks.append("<div>" + "<br>".join(lines) + "</div>")
    return '<div dir="ltr">' + "<div><br></div>".join(blocks) + "</div>"


def build_subject(candidate: Dict, job: Dict) -> str:
    """"Application: <role> - <name> (<n> yrs exp[, <US work status>])". A work status is added only
    for a US-based consultant who has one on file; nothing is assumed."""
    name = candidate.get("name") or "Consultant"
    exp = candidate.get("experience_years")
    bits = [f"{exp} yrs exp"] if exp else []
    visa = (candidate.get("visa_status") or "").strip()
    if visa and _is_us_based(candidate):
        bits.append(visa)
    return f"Application: {job.get('title') or 'Open Role'} - {name}" + (f" ({', '.join(bits)})" if bits else "")


# A sender name like "Devops Engineer" or "Hiring Team" is a role / mailbox, not a person to greet.
_ROLE_WORDS = re.compile(r"\b(engineer|developer|devops|sre|architect|analyst|consultant|admin|administrator|hiring|team|"
                         r"recruit\w*|talent|hr|jobs?|careers?|staffing|solutions|technologies|info|support|manager|lead)\b", re.I)


def person_name(name: str) -> str:
    """'Rakesh Kumar' -> 'Rakesh'; '' for empty / placeholder / role-like names ('Devops Engineer')."""
    n = (name or "").strip().strip('"')
    if not n or n.lower() in ("none", "null") or _ROLE_WORDS.search(n) or "@" in n or any(ch.isdigit() for ch in n):
        return ""
    return n.split()[0].capitalize() if n.split()[0].isalpha() else n.split()[0]


def generate_consultant_pitch(candidate: Dict, job: Dict, custom_notes: str = "", context: Optional[Dict] = None) -> str:
    """A short first-person application email from the consultant to the hiring recruiter.

    Only facts on file are used: no visa/C2C wording for a consultant outside the US, no "H1B"
    when no status is recorded, the job source only when it's a real site (never "Manual Paste"),
    and no copy of the pasted requirement - the email names the consultant's skills that the
    requirement actually mentions. custom_notes is the recruiter's emphasis instruction (e.g.
    "Emphasize AWS"); it steers which skills come first and is never pasted into the email.

    context (answering a requirement email, see inbox_requirements): {"reply": True} thanks them for
    sharing the requirement instead of "I came across your opening"; "skills" = the skills the match
    analysis found in BOTH the requirement and the resume; "tailored" = the attached resume was updated
    for this requirement."""
    context = context or {}
    name = candidate.get("name") or "Consultant"
    title = (candidate.get("title") or "").strip()
    exp = candidate.get("experience_years")
    phone = (candidate.get("phone") or "").strip()
    email_addr = candidate.get("gmail_account") or candidate.get("email") or ""
    linkedin = _linkedin_of(candidate)
    us_based = _is_us_based(candidate)
    visa = (candidate.get("visa_status") or "").strip()

    job_title = job.get("title") or "this role"
    company = (job.get("company") or "").strip()
    company_part = f" at {company}" if company.lower() not in _PLACEHOLDER_COMPANIES else ""
    site = _source_site(job.get("source"))
    first = person_name(job.get("recruiter_name") or "")
    if context.get("reply"):
        greeting = f"Hi {first}," if first else "Hi,"
        intro = f"Thank you for sharing the {job_title} requirement{company_part}. I am very interested in this position."
    else:
        greeting = f"Hi {first or 'Hiring Team'},"
        intro = f"I came across your opening for {job_title}{company_part}" + (f" on {site}" if site else "") + " and I am very interested in it."

    who = f"I am {name}"
    if title and exp:
        who += f", {_article(title)} {title} with {exp} years of experience"
    elif title:
        who += f", {_article(title)} {title}"
    elif exp:
        who += f", with {exp} years of experience"
    if us_based and visa:
        who += f" ({visa})"
    who += "."

    job_text = " ".join([job.get("description") or "", job.get("full_description") or "", job.get("title") or ""])
    matched = [str(x).strip() for x in (context.get("skills") or []) if str(x).strip()][:5] or matching_skills(candidate, job_text, custom_notes)
    if matched:
        fit = f"My hands-on experience in {', '.join(matched[:-1]) + ' and ' + matched[-1] if len(matched) > 1 else matched[0]} matches the key requirements of this role."
    else:
        all_skills = [s.strip() for s in (candidate.get("primary_skills") or "").split(",") if s.strip()][:4]
        fit = f"My core skills include {', '.join(all_skills)}." if all_skills else ""

    closing = ("I have attached my updated resume, tailored to this requirement, for your review." if context.get("tailored")
               else "Please find my resume attached for your review.") + " I am available for an interview at your convenience"
    closing += " and open to contract (C2C / W2) opportunities." if us_based else " and happy to share any further details you need."

    signature = [name] + ([title] if title else []) + ([f"Phone: {phone}"] if phone else []) \
        + ([f"Email: {email_addr}"] if email_addr else []) + ([f"LinkedIn: {linkedin}"] if linkedin else [])

    paragraphs = [greeting, intro, " ".join(x for x in (who, fit) if x), closing,
                  "Looking forward to hearing from you.", "Best regards,\n" + "\n".join(signature)]
    return "\n\n".join(paragraphs) + "\n"


def create_candidate_draft(candidate_id: int, job_id: int, custom_to_email: Optional[str] = None, custom_notes: str = "", custom_subject: Optional[str] = None, custom_body: Optional[str] = None, bcc: Optional[List[str]] = None, save_to_email: bool = True, attachment: Optional[Tuple[str, bytes]] = None, acting_user_id: Optional[int] = None,
                          resume_version_id: Optional[int] = None, reply_to: Optional[Dict[str, str]] = None,
                          pitch_context: Optional[Dict] = None) -> Dict[str, Any]:
    cand = models.get_candidate_by_id(candidate_id)
    if not cand:
        return {"success": False, "error": f"Candidate #{candidate_id} not found."}

    job = models.get_job_by_id(job_id)
    if not job:
        return {"success": False, "error": f"Job #{job_id} not found."}

    to_email = (custom_to_email or job.get("recruiter_email") or "").strip()
    if not to_email or "@" not in to_email:
        return {
            "success": False, 
            "needs_email": True, 
            "error": "Recruiter email is missing. Please type recruiter email directly in the table cell."
        }

    if save_to_email and custom_to_email and custom_to_email != job.get("recruiter_email"):
        models.update_job_recruiter_info(job_id, email=custom_to_email)

    cand_name = cand.get("name", "Consultant")
    cand_title = cand.get("title", "Specialist")
    job_title = job.get("title", "Technical Role")
    company = job.get("company", "Company")
    sender_email = cand.get("gmail_account") or cand.get("email")

    subject = (custom_subject.strip() if custom_subject and custom_subject.strip() else build_subject(cand, job))
    if reply_to and not (custom_subject and custom_subject.strip()):
        # answering a requirement email: "Re: <their subject>" so it stays in the vendor's thread
        their = (reply_to.get("subject") or "").strip()
        subject = their if their.lower().startswith("re:") else f"Re: {their}" if their else subject
    body_text = (custom_body.strip() if custom_body and custom_body.strip() else generate_consultant_pitch(cand, job, custom_notes, pitch_context))

    msg = EmailMessage()
    msg["To"] = to_email
    # Vendor contacts (vendors.py) go in BCC so no vendor sees another's address. The Bcc header is
    # kept in the saved draft (IMAP append / Gmail API) and Gmail strips it when the draft is sent.
    bcc = [b for b in dict.fromkeys((x or "").strip() for x in bcc or []) if "@" in b and b.lower() != to_email.lower()]
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["From"] = sender_email
    msg["Subject"] = subject
    # Our own Message-ID, stored on the application: a recruiter's reply quotes it (In-Reply-To), so the
    # reply is matched to this application and our answer lands in the same Gmail thread.
    message_id = make_msgid(domain=(sender_email or "jobhunt.local").rsplit("@", 1)[-1])
    msg["Message-ID"] = message_id
    if reply_to and reply_to.get("message_id"):
        msg["In-Reply-To"] = reply_to["message_id"]
        msg["References"] = " ".join(x for x in (reply_to.get("references") or "", reply_to["message_id"]) if x).strip()
    # Plain text for clients that want it + the formatted HTML version (what Outlook / Gmail show).
    msg.set_content(body_text)
    msg.add_alternative(render_email_html(body_text), subtype="html")

    # Attach the ORIGINAL resume file. Checked in the database first: Render wipes the on-disk
    # data/resumes/ folder on every deploy, so a file uploaded before the last deploy is gone from
    # disk but still in resume_files (see models.save_resume_file / models.get_resume_file, added
    # for the Resume Optimizer's in-place editing) - without this, a draft would go out with no
    # resume attached, silently, for any consultant uploaded before the app's last redeploy.
    # The same lookup the Resume Optimizer uses (models.find_resume_file): database, then the stored
    # path, then the same file name in this server's data/resumes - a resume_path saved by an
    # earlier server no longer exists after a redeploy, which left drafts without the resume.
    resume_attached, resume_note = False, ""
    file_name, file_data = None, None
    # A saved JD-tailored version (resume_versions) when the caller chose one, else the original resume.
    found = attachment or models.find_resume_file(cand)
    if found:
        file_name, file_data = found

    if file_data:
        mime_type, _ = mimetypes.guess_type(file_name)
        maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
        msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)
        resume_attached = True
    else:
        resume_note = "No resume file is on record for this consultant - the draft was created without an attachment. Upload their resume on the Consultants tab and try again."

    # 1. METHOD A: Using Gmail App Password (IMAP Append to [Gmail]/Drafts)
    app_password = cand.get("gmail_app_password")
    if app_password and sender_email:
        try:
            clean_pass = app_password.replace(" ", "").strip()
            mail = imaplib.IMAP4_SSL("imap.gmail.com", port=993)
            mail.login(sender_email, clean_pass)
            
            # Select or append to Drafts folder
            draft_folder = '"[Gmail]/Drafts"'
            mail.append(draft_folder, "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            mail.logout()

            draft_id = f"imap-draft-{int(time.time())}"
            models.mark_job_drafted(
                job_id=job_id,
                candidate_id=candidate_id,
                draft_id=draft_id,
                user_id=acting_user_id or 1, recruiter_email=to_email, resume_version_id=resume_version_id, outreach_message_id=message_id,
                notes=f"Created Gmail Draft via App Password for {cand_name} -> {to_email}"
            )
            models.log_activity(
                user_id=1,
                user_name="Recruiter",
                action="Gmail Draft Created (App Password)",
                target_type="Job Application",
                target_id=job_id,
                details=f"Drafted application in {sender_email} for {job_title} at {company} ({to_email})"
            )
            return {
                "success": True,
                "method": "app_password",
                "draft_id": draft_id,
                "to_email": to_email,
                "subject": subject,
                "candidate_name": cand_name,
                "company": company,
                "resume_attached": resume_attached,
                "resume_note": resume_note,
                "resume_filename": file_name if resume_attached else "",
                "bcc": bcc,
                "preview": body_text[:280] + "..."
            }
        except Exception as e:
            return {"success": False, "error": f"IMAP App Password Error: {str(e)}"}

    # 2. METHOD B: Using OAuth Token
    token_path = TOKENS_DIR / f"token_{candidate_id}.json"
    if not token_path.exists() and cand.get("gmail_token_path") and Path(cand["gmail_token_path"]).exists():
        token_path = Path(cand["gmail_token_path"])

    if token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())

            if creds and creds.valid:
                service = build("gmail", "v1", credentials=creds)
                raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
                draft_body = {"message": {"raw": raw_msg}}
                draft = service.users().drafts().create(userId="me", body=draft_body).execute()
                draft_id = draft.get("id")

                models.mark_job_drafted(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    user_id=acting_user_id or 1, recruiter_email=to_email, resume_version_id=resume_version_id, outreach_message_id=message_id,
                    notes=f"Created 1-Click Gmail Draft for {cand_name} -> {to_email}"
                )
                models.log_activity(
                    user_id=1,
                    user_name="Recruiter",
                    action="Gmail Draft Created (OAuth)",
                    target_type="Job Application",
                    target_id=job_id,
                    details=f"Drafted application for {cand_name} -> {job_title} at {company} ({to_email})"
                )
                return {
                    "success": True,
                    "method": "oauth",
                    "draft_id": draft_id,
                    "to_email": to_email,
                    "subject": subject,
                    "candidate_name": cand_name,
                    "company": company,
                    "resume_attached": resume_attached,
                    "resume_note": resume_note,
                    "resume_filename": file_name if resume_attached else "",
                    "bcc": bcc,
                    "preview": body_text[:280] + "..."
                }
        except Exception as e:
            print(f"[-] OAuth draft error: {e}")

    # Neither connected
    auth_url, _ = get_auth_url(candidate_id)
    return {
        "success": False,
        "needs_auth": True,
        "auth_url": auth_url,
        "error": f"Gmail not connected for {cand['name']}. Connect via App Password or Google OAuth."
    }
