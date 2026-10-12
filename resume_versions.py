"""Saved, JD-tailored resume versions (table optimized_resumes) - one row per version the recruiter
chose to SAVE after reviewing / editing the optimizer's output. Nothing is saved automatically.

- File name: <ConsultantName>_<PrimarySkill>.docx (e.g. Koushik_Java_Full_Stack.docx); a name already
  used for that consultant gets _v2, _v3 ... - an existing version is never overwritten.
- Primary skill: the optimizer's strongest matched skill that the JD actually names; otherwise the
  first one it matched; otherwise the consultant's first listed primary skill; otherwise their title.
- The saved Word file keeps the original formatting whenever possible: the optimizer's in-place
  edited .docx when the recruiter didn't touch the text, the recruiter's line edits written into that
  same file paragraph by paragraph when the line count is unchanged, and only otherwise a clean Word
  file rebuilt from the text (the response says which).
"""
import base64
import io
import re
from typing import Dict, List, Optional, Tuple

import docx_editor
import models

MAX_DOCX_BYTES = 5 * 1024 * 1024


def _clean(part: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", part or "")).strip("_")


_ROLE_WORD = r"(?:Engineer|Developer|Architect|Analyst|Administrator|Consultant|Scientist|Specialist|Manager|Lead|SRE|Tester|Designer)"
_ROLE_LINE_RE = re.compile(r"^(?:job\s*title|title|role|position|designation)?\s*[:\-]?\s*(.{3,70})$", re.I)
_LOOKING_RE = re.compile(r"(?:looking for|hiring|seeking|need)\s+(?:an?\s+|the\s+)?(?:(?:experienced|skilled|talented|strong|seasoned|motivated)\s+)*"
                         r"((?:[A-Z][\w/&+.#-]*\s+){0,4}" + _ROLE_WORD + r")\b")


def jd_role(jd_text: str) -> str:
    """The role the JD hires for ('DevOps Engineer'), from its title line or 'looking for an experienced X'."""
    lines = [l.strip() for l in (jd_text or "").splitlines() if l.strip()][:6]
    for line in lines:
        # job-board headers run on: "DevOps Engineer DEPARTMENTDevelopment EMPLOYMENT TYPEFull Time ..." (labels in capitals)
        line = re.split(r"\s+(?:DEPARTMENT|LOCATION|EMPLOYMENT|EXPERIENCE|SALARY|JOB TYPE)", line)[0].strip()
        m = _ROLE_LINE_RE.match(line)
        if m and re.search(r"\b" + _ROLE_WORD + r"\b", m.group(1)) and not line.endswith((".", ":")) and len(m.group(1).split()) <= 8:
            role = re.sub(r"\s*[\(\[].*$", "", m.group(1))
            return " ".join(role.split()[:6]).strip(" -|,")
    m = _LOOKING_RE.search(jd_text or "")
    return m.group(1).strip() if m else ""


def primary_skill(result: Dict, cand: Optional[Dict], jd_text: str) -> str:
    """The label in the saved file's name: the JD's role + the matched skill the JD mentions most
    ('DevOps Engineer Terraform'), else that skill alone, else the consultant's main skill."""
    matched = [s for s in (result or {}).get("mandatory_matched_skills") or [] if str(s).strip()]
    jd = (jd_text or "").lower()
    in_jd = [s for s in matched if str(s).strip().lower() in jd]
    # the skill the JD talks about most (not just the first one listed)
    in_jd.sort(key=lambda s: -jd.count(str(s).strip().lower()))
    role = jd_role(jd_text)
    if role:
        top = next((str(s).strip() for s in in_jd if str(s).strip().lower() not in role.lower()), "")
        return f"{role} {top}".strip()
    if in_jd:
        return str(in_jd[0]).strip()
    if matched:
        return str(matched[0]).strip()
    skills = [s.strip() for s in str((cand or {}).get("primary_skills") or "").split(",") if s.strip()]
    if skills:
        return skills[0]
    return str((cand or {}).get("title") or "Resume").strip()


def make_filename(consultant_name: str, skill: str, taken: List[str]) -> str:
    """<Name>_<Skill>.docx, with _v2/_v3... when that name is already used for this consultant."""
    name = _clean(consultant_name) or "Consultant"
    sk = _clean(skill)[:40].strip("_") or "Resume"
    base = f"{name}_{sk}"
    used = {t.lower() for t in taken or []}
    candidate, n = f"{base}.docx", 1
    while candidate.lower() in used:
        n += 1
        candidate = f"{base}_v{n}.docx"
    return candidate


def _norm_lines(text: str) -> List[str]:
    return [re.sub(r"\s+", " ", l).strip() for l in (text or "").replace("\r\n", "\n").split("\n") if l.strip()]


def build_final_docx(ai_docx: Optional[bytes], ai_text: str, edited_text: str, consultant_name: str) -> Tuple[bytes, bool, str]:
    """(docx bytes, formatting_kept, note) for what the recruiter approved."""
    edited = _norm_lines(edited_text)
    if ai_docx:
        if edited == _norm_lines(ai_text):
            return ai_docx, True, ""
        document, paragraphs = docx_editor.load_paragraphs(ai_docx)
        if all("\n" not in p["text"] for p in paragraphs) and len(paragraphs) == len(edited):
            changed = [(p, new) for p, new in zip(paragraphs, edited) if re.sub(r"\s+", " ", p["text"]).strip() != new]
            if not any(p["locked"] for p, _ in changed):
                for p, new in changed:
                    docx_editor._replace_text(p["paragraph"], new)
                out = io.BytesIO()
                document.save(out)
                return out.getvalue(), True, ""
        note = ("Your edits added or removed lines (or changed a linked line), so the saved Word file was rebuilt from the text - "
                "check its formatting before sending.")
    else:
        note = "This resume had no original Word file, so the saved file is a clean Word document built from the text."
    import resume_bot
    return resume_bot.create_docx_resume(edited_text, consultant_name).getvalue(), False, note


# ---------------------------------------------------------------- storage

def taken_names(candidate_id: int) -> List[str]:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT filename FROM optimized_resumes WHERE candidate_id = ?", (candidate_id,))
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def save(candidate_id: int, job_id: Optional[int], filename: str, data: bytes, skill: str, jd_cloud: str,
         jd_excerpt: str, user_id: int) -> int:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""INSERT INTO optimized_resumes (candidate_id, job_id, filename, data, primary_skill, jd_cloud, jd_excerpt, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (candidate_id, job_id, filename, bytes(data), skill[:120], (jd_cloud or "")[:20], (jd_excerpt or "")[:300], user_id))
        new_id = cur.lastrowid
        conn.commit()
        if not new_id:
            cur.execute("SELECT MAX(id) FROM optimized_resumes WHERE candidate_id = ?", (candidate_id,))
            new_id = cur.fetchone()[0]
        return int(new_id)
    finally:
        conn.close()


def get(version_id: int) -> Optional[Dict]:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM optimized_resumes WHERE id = ?", (version_id,))
        r = cur.fetchone()
        if not r:
            return None
        r = dict(r)
        r["data"] = bytes(r["data"]) if r.get("data") is not None else b""
        return r
    finally:
        conn.close()


def list_for(candidate_id: int) -> List[Dict]:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""SELECT id, candidate_id, job_id, filename, primary_skill, jd_cloud, jd_excerpt, created_by, created_at
                       FROM optimized_resumes WHERE candidate_id = ? ORDER BY id DESC""", (candidate_id,))
        return [{**dict(r), "created_at": str(r["created_at"])[:19]} for r in cur.fetchall()]
    finally:
        conn.close()


def latest_for_job(candidate_id: int, job_id: int) -> Optional[Dict]:
    conn = models.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM optimized_resumes WHERE candidate_id = ? AND job_id = ? ORDER BY id DESC LIMIT 1", (candidate_id, job_id))
        r = cur.fetchone()
    finally:
        conn.close()
    return get(r[0]) if r else None


def decode_docx(b64: str) -> Optional[bytes]:
    if not b64:
        return None
    try:
        data = base64.b64decode(b64, validate=True)
    except Exception:
        return None
    return data if 0 < len(data) <= MAX_DOCX_BYTES and data[:2] == b"PK" else None
