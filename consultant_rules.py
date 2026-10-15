"""Standing instructions per consultant ("📌 Rules for Abhinov"), set by the recruiter once and applied to
every email and resume written for that consultant.

- kind 'email': followed by the application-email writer (application_brief.write), the email chats
  (outreach_ai.edit_draft) and the replies prepared for requirements from Gmail.
- kind 'resume': followed by the resume chat (resume_editor.edit) and passed to the Resume Optimizer as
  custom instructions.
A rule never licenses invented facts: the writers' guards still refuse a rate / years / skill that isn't on
file - but a fact written in a rule counts as the recruiter's own words.
"""
import re
from typing import Dict, List, Optional

KINDS = ("email", "resume")
MAX_PER_KIND = 20
MAX_LEN = 500


class RuleError(ValueError):
    pass


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(t) < 3:
        raise RuleError("Write the rule first.")
    return t[:MAX_LEN]


def list_for(conn, candidate_id: int, kind: Optional[str] = None) -> List[Dict]:
    cur = conn.cursor()
    if kind:
        cur.execute("SELECT id, candidate_id, kind, text, created_at FROM consultant_rules WHERE candidate_id = ? AND kind = ? ORDER BY id",
                    (candidate_id, kind))
    else:
        cur.execute("SELECT id, candidate_id, kind, text, created_at FROM consultant_rules WHERE candidate_id = ? ORDER BY kind, id", (candidate_id,))
    return [{"id": r[0], "candidate_id": r[1], "kind": r[2], "text": r[3], "created_at": str(r[4] or "")[:16]} for r in cur.fetchall()]


def add(conn, candidate_id: int, kind: str, text: str, user_id: Optional[int] = None) -> int:
    if kind not in KINDS:
        raise RuleError("A rule is for emails or for the resume.")
    t = _clean(text)
    have = list_for(conn, candidate_id, kind)
    if any(r["text"].lower() == t.lower() for r in have):
        raise RuleError("That rule is already saved.")
    if len(have) >= MAX_PER_KIND:
        raise RuleError(f"At most {MAX_PER_KIND} {kind} rules per consultant - delete one first.")
    cur = conn.cursor()
    cur.execute("INSERT INTO consultant_rules (candidate_id, kind, text, created_by) VALUES (?, ?, ?, ?)", (candidate_id, kind, t, user_id))
    rid = cur.lastrowid
    conn.commit()
    if not rid:
        cur.execute("SELECT id FROM consultant_rules WHERE candidate_id = ? AND kind = ? AND text = ?", (candidate_id, kind, t))
        rid = cur.fetchone()[0]
    return rid


def update(conn, candidate_id: int, rule_id: int, text: str) -> None:
    t = _clean(text)
    cur = conn.cursor()
    cur.execute("UPDATE consultant_rules SET text = ? WHERE id = ? AND candidate_id = ?", (t, rule_id, candidate_id))
    conn.commit()


def delete(conn, candidate_id: int, rule_id: int) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM consultant_rules WHERE id = ? AND candidate_id = ?", (rule_id, candidate_id))
    conn.commit()


def text_for(candidate_id, kind: str) -> str:
    """The rules as numbered lines for a prompt ('' when there are none). Never raises."""
    try:
        cid = int(candidate_id)
    except (TypeError, ValueError):
        return ""
    try:
        import models
        conn = models.get_db_connection()
        try:
            rules = list_for(conn, cid, kind)
        finally:
            conn.close()
    except Exception:
        return ""
    return "\n".join(f"{i}. {r['text']}" for i, r in enumerate(rules, 1))


def prompt_block(candidate_id, kind: str) -> str:
    rules = text_for(candidate_id, kind)
    if not rules:
        return ""
    what = "email" if kind == "email" else "resume"
    return (f"CONSULTANT RULES - the recruiter's standing instructions for every {what} of this consultant. Follow them "
            f"unless the recruiter's current message says otherwise (they never allow inventing facts - use [please fill: ...]):\n{rules}\n\n")
