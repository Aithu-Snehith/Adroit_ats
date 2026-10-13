"""USA / India teams in one app.

- Every user has users.region ('USA' or 'India'). Recruiters are locked to it; admins choose a view
  (USA / India / Both) with the switcher in the top bar (kept in the session).
- A consultant's region comes from their country: 'India' -> India team, anything else -> USA team
  (no extra column - a consultant moves team when their country changes).
- What is split: consultants / bench (and everything reached through a consultant: drafts, resume
  optimizer, applications), the Jobs feed (US jobs vs India jobs) and the admins' Vendors view.
- backfill() gives existing users a region once: India when most of their consultants are in India,
  otherwise USA. Admins can change any recruiter's region in Admin & Settings.
"""
from typing import Dict, Iterable, Optional, Set

REGIONS = ("USA", "India")
JOB_COUNTRY = {"USA": "United States", "India": "India"}


def normalize(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return "India" if v in ("india", "in") else ("USA" if v in ("usa", "us", "united states") else "")


def candidate_region(cand: Dict) -> str:
    return "India" if (cand.get("country") or "").strip().lower() == "india" else "USA"


def user_region(conn, user_id: int) -> str:
    cur = conn.cursor()
    cur.execute("SELECT region FROM users WHERE id = ?", (user_id,))
    r = cur.fetchone()
    return normalize(r[0] if r else "") or "USA"


def view_regions(user: Dict, stored_region: str, admin_view: Optional[str]) -> Set[str]:
    """Regions this user is looking at right now."""
    if "Admin" in (user.get("role") or ""):
        v = normalize(admin_view)
        return {v} if v else set(REGIONS)
    return {stored_region or "USA"}


def filter_candidates(cands: Iterable[Dict], regions: Set[str]):
    return [c for c in cands if candidate_region(c) in regions]


def backfill(conn) -> int:
    """Give users without a region one (India when most of their consultants are in India)."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE region IS NULL OR region = ''")
    ids = [r[0] for r in cur.fetchall()]
    for uid in ids:
        cur.execute("""SELECT SUM(CASE WHEN LOWER(COALESCE(country, '')) = 'india' THEN 1 ELSE 0 END),
                              SUM(CASE WHEN LOWER(COALESCE(country, '')) = 'india' THEN 0 ELSE 1 END)
                       FROM candidates WHERE assigned_user_id = ?""", (uid,))
        row = cur.fetchone()        # by position: a Postgres row (models.DbRow) iterates its column names
        india, other = row[0], row[1]
        cur.execute("UPDATE users SET region = ? WHERE id = ?", ("India" if (india or 0) > (other or 0) else "USA", uid))
    conn.commit()
    return len(ids)
