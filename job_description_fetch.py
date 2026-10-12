"""Fetch the FULL job description of a scraped posting, on demand (one posting per click).

Scraped jobs only store a one-line summary, which is too little for the Resume Optimizer. When a
recruiter clicks "Optimize Resume" on a LinkedIn or Dice job, this reads that one public posting:

- LinkedIn: the public guest job-posting page (no login), description section
  `div.show-more-less-html__markup`. Same kind of public endpoint the job search already uses.
- Dice: the posting page's schema.org JobPosting JSON-LD `description`.

- Workday / Greenhouse / Lever career sites: their public job APIs (the pages themselves load the
  job with JavaScript).
- Any other public posting (company career sites such as careers.epam.com, Foundit, ...): the page's
  schema.org JobPosting JSON-LD - the block search engines read.
- Naukri: refused with a clear reason - it answers automated reads with a reCAPTCHA.

Free (plain HTTP, no Apify). LinkedIn / Dice / Greenhouse / Lever URLs are rebuilt from the job id.
Any other URL is fetched only if every address its host resolves to is PUBLIC (no localhost, private,
link-local or reserved ranges - so a stored link can't make the server read its own network), on
ports 80/443, following at most 3 redirects (each re-checked), reading at most 3 MB. Nothing is
invented: anything that can't be read returns (None, reason).
"""
import html as html_lib
import ipaddress
import socket
import json
import logging
import re
import urllib.parse
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_description_fetch")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15
MIN_CHARS = 200
MAX_CHARS = 30000

_LINKEDIN_ID_RES = (
    re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})(?:[/?#]|$)"),
    re.compile(r"[?&](?:currentJobId|jobId)=(\d{6,})"),
)
_DICE_ID_RE = re.compile(r"/job-detail/([0-9a-fA-F-]{16,64})(?:[/?#]|$)")
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_host(url: str, domain: str) -> bool:
    h = _host(url)
    return h == domain or h.endswith("." + domain)


def linkedin_job_id(url: str) -> Optional[str]:
    if not _is_host(url, "linkedin.com"):
        return None
    for rx in _LINKEDIN_ID_RES:
        m = rx.search(url)
        if m:
            return m.group(1)
    return None


def dice_job_id(url: str) -> Optional[str]:
    if not _is_host(url, "dice.com"):
        return None
    m = _DICE_ID_RE.search(url)
    return m.group(1) if m else None


def supported(url: Optional[str]) -> bool:
    return bool(url) and bool(linkedin_job_id(url) or dice_job_id(url))


def html_to_text(html: str) -> str:
    """Readable plain text: one line per paragraph/heading, "- " for list items, no blank-line runs."""
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert(0, "\n- ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.append("\n")
    text = soup.get_text("")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if out and not blank:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()[:MAX_CHARS]


def _get(url: str) -> Tuple[Optional[str], str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as ex:
        logger.warning("job description fetch failed for %s: %s", url, ex)
        return None, "Could not reach the job site."
    if r.status_code in (404, 410):
        return None, "The posting is no longer available."
    if r.status_code == 429:
        return None, "The job site is limiting requests right now - try again in a minute."
    if r.status_code != 200:
        return None, f"The job site returned an error ({r.status_code})."
    return r.content.decode("utf-8", errors="replace"), ""


def _linkedin(job_id: str) -> Tuple[Optional[str], str]:
    html, err = _get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    if html is None:
        return None, err
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one("div.show-more-less-html__markup") or soup.select_one("div.description__text")
    if not el:
        return None, "The posting page has no job description section (LinkedIn may have changed its page)."
    return html_to_text(str(el)), ""


def _job_posting_description(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(sc.string or sc.get_text() or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else (data.get("@graph") if isinstance(data, dict) and "@graph" in data else [data])
        for it in items or []:
            if isinstance(it, dict) and it.get("@type") == "JobPosting" and it.get("description"):
                return str(it["description"])
    return None


def _dice(job_id: str) -> Tuple[Optional[str], str]:
    html, err = _get(f"https://www.dice.com/job-detail/{job_id}")
    if html is None:
        return None, err
    desc = _job_posting_description(html)
    if not desc:
        return None, "The posting page has no job description data (Dice may have changed its page)."
    return html_to_text(desc), ""


MAX_BYTES = 3 * 1024 * 1024
_resolve = socket.getaddrinfo          # replaced in tests (no DNS)


def _public_host_problem(url: str) -> str:
    """'' when the URL is http(s) on 80/443 and its host resolves only to public addresses."""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return "That link isn't a valid web address."
    if p.scheme not in ("http", "https") or not p.hostname:
        return "That link isn't a web page address."
    if p.port not in (None, 80, 443):
        return "That link uses an unusual port, so it isn't read automatically."
    try:                                   # a raw address in the link is judged as it is, before any lookup
        literal = ipaddress.ip_address(p.hostname.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and (not literal.is_global or literal.is_multicast):
        return "That link points to a private network address, so it isn't read."
    try:
        infos = _resolve(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return "Could not reach the job site."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return "That link points to a private network address, so it isn't read."
    return ""


def _get_public(url: str, extra_headers: Optional[dict] = None) -> Tuple[Optional[str], str]:
    """GET a public page: every hop's host re-checked, at most 3 redirects, at most MAX_BYTES."""
    for _ in range(4):
        problem = _public_host_problem(url)
        if problem:
            return None, problem
        try:
            r = requests.get(url, headers=dict(HEADERS, **(extra_headers or {})), timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as ex:
            logger.warning("job description fetch failed for %s: %s", url, ex)
            return None, "Could not reach the job site."
        if r.status_code in (301, 302, 303, 307, 308) and (getattr(r, "headers", None) or {}).get("Location"):
            url = urllib.parse.urljoin(url, r.headers["Location"])
            continue
        if r.status_code in (404, 410):
            return None, "The posting is no longer available."
        if r.status_code in (401, 403, 406, 429):
            return None, "The job site blocks automatic reading - open the posting and paste the job description."
        if r.status_code != 200:
            return None, f"The job site returned an error ({r.status_code})."
        body = r.content[:MAX_BYTES]
        return body.decode("utf-8", errors="replace"), ""
    return None, "The job site redirected too many times."


def _greenhouse(url: str) -> Optional[Tuple[Optional[str], str]]:
    m = re.match(r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs/(\d+)", url)
    if not m:
        return None
    raw, err = _get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    if raw is None:
        return None, err
    try:
        content = json.loads(raw).get("content") or ""
    except ValueError:
        return None, "The Greenhouse posting couldn't be read."
    return html_to_text(html_lib.unescape(content)), ""


def _lever(url: str) -> Optional[Tuple[Optional[str], str]]:
    m = re.match(r"https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)/([0-9a-f-]{36})", url)
    if not m:
        return None
    raw, err = _get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    if raw is None:
        return None, err
    try:
        d = json.loads(raw)
    except ValueError:
        return None, "The Lever posting couldn't be read."
    parts = [d.get("descriptionPlain") or ""]
    for block in d.get("lists") or []:
        parts.append(block.get("text") or "")
        parts.append(html_to_text(block.get("content") or ""))
    parts.append(d.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())[:MAX_CHARS], ""


def _workday(url: str) -> Optional[Tuple[Optional[str], str]]:
    m = re.match(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/(job/[^?#]+)", url)
    if not m:
        return None
    tenant, wd, site, path = m.groups()
    raw, err = _get_public(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}", {"Accept": "application/json"})
    if raw is None:
        return None, err
    try:
        info = json.loads(raw).get("jobPostingInfo") or {}
    except ValueError:
        return None, "The Workday posting couldn't be read."
    return html_to_text(info.get("jobDescription") or ""), ""


def _generic(url: str) -> Tuple[Optional[str], str]:
    page, err = _get_public(url)
    if page is None:
        return None, err
    desc = _job_posting_description(page)
    if not desc:
        return None, ("This page doesn't include the job description in a readable form (the site loads it with "
                      "JavaScript) - open the posting and paste the job description.")
    return html_to_text(html_lib.unescape(desc) if "&lt;" in desc else desc), ""


def fetch_full_description(url: Optional[str]) -> Tuple[Optional[str], str]:
    """(full description text, "") or (None, reason a recruiter can read)."""
    if not url:
        return None, "This job has no posting link."
    li_id, dice_id = linkedin_job_id(url), dice_job_id(url)
    if li_id:
        text, err = _linkedin(li_id)
        site = "LinkedIn"
    elif dice_id:
        text, err = _dice(dice_id)
        site = "Dice"
    elif _is_host(url, "naukri.com"):
        return None, ("Naukri blocks automatic reading (it asks for a reCAPTCHA) - open the posting and paste the "
                      "job description.")
    else:
        site = "job site"
        for reader in (_greenhouse, _lever, _workday):
            got = reader(url)
            if got is not None:
                text, err = got
                break
        else:
            text, err = _generic(url)
    if text is None:
        return None, err
    if len(text) < MIN_CHARS:
        return None, f"The {site} posting's description is too short to be the full job description."
    return text, ""
