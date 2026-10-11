"""Fill in Open to work / employment type / workplace type for profiles saved BEFORE the app kept them.

The LinkedIn scraper always returned those fields (Full mode); the app used to drop them. The
results of past searches are still stored at the provider for a while, so they can be read back
instead of scraping again:
  - Apify: every past run of the profile-search actor keeps its dataset (free plan: the 10 most
    recent runs for 4 months; paid plans: the account's retention setting). Reading costs about
    $0.0004 per 1,000 items - no actor run, no scraping charge.
  - HarvestAPI direct: the items kept in harvest_runs (only runs made after the fields were kept
    carry them).
Only profiles that have NO work data yet are filled, newest search first, so a newer value is never
overwritten by an older one. Nothing is guessed: a field LinkedIn left blank stays blank.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests

import linkedin_ingest
import linkedin_sourcing

logger = logging.getLogger("work_backfill")

FIELDS = "linkedinUrl,openToWork,experience,currentPosition"
MAX_RUNS = 400           # newest first; older runs have usually expired anyway
TIME_BUDGET_S = 150      # stay well inside gunicorn's 240 s request limit
WORKERS = 5


def _missing(cur) -> Dict[str, int]:
    """linkedin_url -> profile id for every saved profile without any work data."""
    cur.execute("""SELECT id, linkedin_url FROM linkedin_profiles
                   WHERE open_to_work IS NULL AND current_employment_type IS NULL AND current_workplace_type IS NULL""")
    return {r["linkedin_url"]: r["id"] for r in cur.fetchall()}


def _apify_runs(token: str) -> List[Dict]:
    runs, offset = [], 0
    while len(runs) < MAX_RUNS:
        r = requests.get(f"{linkedin_sourcing.API}/acts/{linkedin_sourcing.ACTOR}/runs",
                         params={"token": token, "desc": 1, "limit": 100, "offset": offset}, timeout=20)
        r.raise_for_status()
        items = r.json().get("data", {}).get("items", [])
        runs += [x for x in items if x.get("defaultDatasetId")]
        if len(items) < 100:
            break
        offset += 100
    return runs[:MAX_RUNS]


def _dataset_items(token: str, dataset_id: str) -> Optional[List[Dict]]:
    """The run's items, or None when the dataset has expired / can't be read."""
    try:
        r = requests.get(f"{linkedin_sourcing.API}/datasets/{dataset_id}/items",
                         params={"token": token, "fields": FIELDS, "clean": 1, "limit": 1000}, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json()
        return data if isinstance(data, list) else None
    except Exception as ex:
        logger.warning(f"Could not read dataset {dataset_id}: {ex}")
        return None


def _apply(cur, missing: Dict[str, int], items: List[Dict]) -> int:
    filled = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = linkedin_ingest.canonical_linkedin_url(item.get("linkedinUrl"))
        pid = missing.get(url) if url else None
        if not pid:
            continue
        work = linkedin_ingest.work_info(item)
        if work is None:
            continue
        cur.execute("""UPDATE linkedin_profiles SET open_to_work = ?, current_employment_type = ?, current_workplace_type = ?
                       WHERE id = ?""", (work["open_to_work"], work["current_employment_type"] or None,
                                         work["current_workplace_type"] or None, pid))
        missing.pop(url, None)      # newest search wins; older runs never overwrite it
        filled += 1
    return filled


def run(conn) -> Dict:
    started = time.monotonic()
    cur = conn.cursor()
    missing = _missing(cur)
    stats = {"missing_before": len(missing), "filled": 0, "searches_read": 0, "searches_expired": 0,
             "partial": False, "error": ""}
    if missing:
        # HarvestAPI direct runs stored here (newest first) - free, local.
        cur.execute("SELECT items_json FROM harvest_runs WHERE items_json IS NOT NULL ORDER BY id DESC")
        for row in cur.fetchall():
            try:
                stats["filled"] += _apply(cur, missing, json.loads(row["items_json"] or "[]"))
            except ValueError:
                continue
        conn.commit()
    token = linkedin_sourcing._token()
    if missing and token:
        try:
            runs = _apify_runs(token)
        except Exception as ex:
            stats["error"] = f"Could not list past Apify searches: {ex}"
            runs = []
        # read datasets in parallel, apply strictly newest first
        for i in range(0, len(runs), WORKERS * 4):
            if not missing:
                break
            if time.monotonic() - started > TIME_BUDGET_S:
                stats["partial"] = True
                break
            batch = runs[i:i + WORKERS * 4]
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                results = list(pool.map(lambda r: _dataset_items(token, r["defaultDatasetId"]), batch))
            for items in results:
                if items is None:
                    stats["searches_expired"] += 1
                    continue
                stats["searches_read"] += 1
                stats["filled"] += _apply(cur, missing, items)
            conn.commit()
    stats["still_missing"] = len(missing)
    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats
