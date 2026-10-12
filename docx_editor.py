"""Edits an uploaded .docx IN PLACE so the optimized resume keeps the original file's look.

The AI never rewrites the whole resume here. It is shown the original paragraphs, numbered, and
answers with a short list of edits ("replace paragraph 12", "insert a bullet after paragraph 20").
Those edits are applied to the original document, so everything not edited - fonts, sizes,
colours, tables, bullets, margins, headers/footers, section order - is left exactly as it was.
"""
import copy
import io
import re

import docx
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

# A career/education year: 1970-2039. (Not "1920" in a "1080x1920 video" line, which is a pixel size.)
_YEAR_RE = re.compile(r"\b(?:19[7-9]\d|20[0-3]\d)\b")
_YEARS_EXP_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", re.I)

MAX_EDITS = 40
MAX_REPLACE_CHARS = 1500
MAX_INSERT_CHARS = 500


def _iter_paragraphs(parent_elm, parent):
    """Paragraphs in document order, including those inside tables (résumés often use a table
    for layout) and content controls."""
    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            table = Table(child, parent)
            for tr in child.iterchildren(qn("w:tr")):
                for tc in tr.iterchildren(qn("w:tc")):
                    yield from _iter_paragraphs(tc, _Cell(tc, table))
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _iter_paragraphs(content, parent)


def _style_name(paragraph):
    try:
        return (paragraph.style.name or "").lower() if paragraph.style is not None else ""
    except Exception:
        return ""


def _is_bullet(paragraph):
    pPr = paragraph._p.pPr
    if pPr is not None and pPr.numPr is not None:
        return True
    if "list" in _style_name(paragraph):
        return True
    return paragraph.text.lstrip()[:1] in ("•", "▪", "●", "◦", "-", "*", "–")


def _is_heading(paragraph):
    """Section headings, names and job-title lines are never edited: the master prompt says to
    keep section order and titles as they are."""
    name = _style_name(paragraph)
    if name.startswith(("heading", "title")):
        return True
    text = paragraph.text.strip()
    if not text or len(text) > 45:
        return False
    if text.upper() == text and re.search(r"[A-Z]", text):
        return True
    runs = [r for r in paragraph.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs) and not text.endswith((".", ","))


def _is_title_line(paragraph):
    """A short non-bullet line that opens with bold text ('Acme Corp - Senior Developer') is a
    company / job-title line. Bold labels that end in a colon ('Environment:') are not."""
    text = paragraph.text.strip()
    if not text or len(text) > 120 or _is_bullet(paragraph):
        return False
    first = next((r for r in paragraph.runs if r.text.strip()), None)
    return first is not None and bool(first.bold) and not first.text.rstrip().endswith(":")


_PLAIN_PARAGRAPH_CHILDREN = {qn(t) for t in ("w:pPr", "w:r", "w:bookmarkStart", "w:bookmarkEnd", "w:proofErr")}


def _is_plain(paragraph):
    """True when the paragraph is only ordinary text runs. Anything else - hyperlinks, tracked
    changes, fields, smart tags, content controls - holds content that python-docx's text/runs
    view can't fully see, so an edit could leave it half-changed or flatten a link."""
    if any(child.tag not in _PLAIN_PARAGRAPH_CHILDREN for child in paragraph._p.iterchildren()):
        return False
    return not paragraph._p.xpath(".//w:fldChar | .//w:instrText")


def load_paragraphs(docx_bytes):
    """(document, paragraphs). `paragraphs` lists the non-empty paragraphs in document order;
    each one's `index` is the number the AI refers to. A paragraph that holds anything besides
    plain text runs (a hyperlink, tracked change, field...) is `locked` - it can be read but
    never edited, because editing it could flatten the link or leave the change half-applied."""
    document = docx.Document(io.BytesIO(docx_bytes))
    paragraphs = []
    for p in _iter_paragraphs(document.element.body, document):
        text = p.text
        if not text.strip():
            continue
        runs_text = "".join(r.text for r in p.runs)
        paragraphs.append({
            "index": len(paragraphs),
            "paragraph": p,
            "text": text,
            "bullet": _is_bullet(p),
            "heading": _is_heading(p) or _is_title_line(p),
            "locked": runs_text != text or not _is_plain(p),
        })
    return document, paragraphs


def extract_text(docx_bytes):
    _, paragraphs = load_paragraphs(docx_bytes)
    return "\n".join(p["text"] for p in paragraphs).strip()


def numbered_listing(paragraphs):
    """The resume as the AI sees it: one numbered line per paragraph, with tags for the ones
    it must not touch."""
    lines = []
    for p in paragraphs:
        tags = []
        if p["locked"]:
            tags.append("locked - do not edit")
        elif p["heading"]:
            tags.append("heading/title line - do not edit")
        elif p["bullet"]:
            tags.append("bullet")
        prefix = f"[{p['index']}]" + (f" ({', '.join(tags)})" if tags else "")
        lines.append(f"{prefix} {p['text']}")
    return "\n".join(lines)


def _has_drawing(run):
    return bool(run._r.xpath(".//w:drawing | .//w:pict | .//w:object"))


def _replace_text(paragraph, new_text):
    """Sets the paragraph's text while keeping its run formatting: the runs covering the text
    that stays the same are untouched, and only the changed tail goes into the run where the
    change starts (so a bold 'Environment:' label stays bold and the appended items inherit
    the value's normal formatting)."""
    runs = list(paragraph.runs)
    old = "".join(r.text for r in runs)
    i, limit = 0, min(len(old), len(new_text))
    while i < limit and old[i] == new_text[i]:
        i += 1
    pos, target = 0, len(runs) - 1
    for k, run in enumerate(runs):
        end = pos + len(run.text)
        if i < end or (k == len(runs) - 1):
            target = k
            break
        pos = end
    run = runs[target]
    if _has_drawing(run):
        return False
    off = i - pos
    run.text = run.text[:off] + new_text[i:]
    for later in runs[target + 1:]:
        if not _has_drawing(later):
            later._r.getparent().remove(later._r)
    return True


def _clone_after(anchor_el, template, text):
    """Adds a new paragraph right after `anchor_el` that copies `template`'s paragraph
    properties (bullet/numbering, indents, spacing, alignment) and its first text run's
    character formatting."""
    new_p = copy.deepcopy(template._p)
    first_run = None
    for child in list(new_p):
        if child.tag == qn("w:pPr"):
            for sect in child.findall(qn("w:sectPr")):   # never duplicate a section break
                child.remove(sect)
            continue
        if first_run is None and child.tag == qn("w:r") and not child.xpath(".//w:drawing | .//w:pict"):
            first_run = child
            continue
        new_p.remove(child)
    para = Paragraph(new_p, template._parent)
    if first_run is None:
        para.add_run("")
    run = para.runs[0]
    for br in run._r.xpath(".//w:br"):           # a copied page break would split the resume
        br.getparent().remove(br)
    run.text = text
    anchor_el.addnext(new_p)
    return new_p


def edit_problem(old, new, inserting=False):
    """Why an edit is refused, or '' when it is acceptable. These are the master prompt's NEVER
    rules that can be checked mechanically, applied to each edit on its own so one bad edit
    doesn't throw away the good ones."""
    if not new.strip():
        return "the new text was empty"
    if not inserting and _YEAR_RE.search(old):
        return "that line contains dates, which must never change"
    if set(_YEAR_RE.findall(new)) - set(_YEAR_RE.findall(old)):
        return "the new text adds a year/date"
    if set(_YEARS_EXP_RE.findall(new)) - set(_YEARS_EXP_RE.findall(old)):
        return "the new text adds a years-of-experience claim"
    limit = MAX_INSERT_CHARS if inserting else MAX_REPLACE_CHARS
    if len(new) > limit:
        return f"the new text is longer than {limit} characters"
    return ""


_TAG_RE = re.compile(r"^\((?:bullet|heading/title line - do not edit|locked - do not edit)\)\s*")
_LINE_RE = re.compile(r"^\[(\d+|\+\d*)\]\s?(.*)$")


def _norm(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_numbered(text):
    """Reads the AI's whole-resume answer, one '[12] text' line per paragraph (and '[+] text' for a
    new paragraph). Returns [[kind, index-or-None, text], ...] with kind 'n' or '+'. A line with no
    tag continues the previous paragraph (paragraphs that contain manual line breaks); code fences
    and the tags the AI was shown are dropped."""
    entries = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            continue
        m = _LINE_RE.match(line.strip())
        if m:
            tag, body = m.groups()
            if tag.startswith("+"):   # "[+]" = after the previous line; "[+12]" = after paragraph 12
                entries.append(["+", int(tag[1:]) if tag[1:] else None, _TAG_RE.sub("", body.lstrip())])
            else:
                entries.append(["n", int(tag), _TAG_RE.sub("", body.lstrip())])
        elif entries and line.strip():
            entries[-1][2] += "\n" + line.rstrip()
    return entries


def edits_from_rewrite(entries, paragraphs):
    """Turns the AI's complete rewritten resume into edits against the original paragraphs:
    a paragraph whose text differs (ignoring spacing) becomes a 'replace', a '[+]' line becomes an
    'insert_after' its predecessor. Paragraphs the AI left out are simply unchanged - nothing is
    ever deleted. Returns (edits, number of distinct original paragraphs the AI returned)."""
    originals = {p["index"]: p["text"] for p in paragraphs}
    edits, seen, anchor = [], set(), None
    for kind, idx, body in entries:
        if kind == "n":
            if idx not in originals or idx in seen:
                continue
            seen.add(idx)
            anchor = idx
            # Untagged lines only belong to a paragraph that really had manual line breaks; anything
            # beyond that is the model talking ("Note: I added...") and must not enter the resume.
            body = "\n".join(body.strip().split("\n")[:originals[idx].count("\n") + 1]).strip()
            if body and _norm(body) != _norm(originals[idx]):
                edits.append({"op": "replace", "paragraph": idx, "new_text": body})
        else:
            body = body.strip().split("\n")[0].strip()          # a new paragraph is one line
            if idx is not None and idx in originals:
                anchor = idx                                      # "[+12]": explicitly after paragraph 12
            if anchor is not None and body:
                edits.append({"op": "insert_after", "paragraph": anchor, "new_text": body})
    return edits, len(seen)


def apply_edits(docx_bytes, edits, extra_check=None, allow_headings=False, allow_delete=False):
    """Applies the AI's edits to the original document. Returns (new_docx_bytes, applied,
    skipped). Every edit is checked on its own; refused ones are reported with the reason.
    `extra_check(old_text, new_text, inserting)` may return a further reason to refuse an edit.
    The resume chat (resume_editor) may also pass allow_headings (the recruiter asked to change a
    headline / title - lines with dates still never change) and allow_delete ('delete' edits)."""
    document, paragraphs = load_paragraphs(docx_bytes)
    by_index = {p["index"]: p for p in paragraphs}
    applied, skipped = [], []
    replaced, last_inserted = set(), {}

    def skip(edit, reason):
        skipped.append({"paragraph": edit.get("paragraph"), "op": edit.get("op"),
                        "new_text": str(edit.get("new_text") or "")[:200], "reason": reason})

    for edit in (edits if isinstance(edits, list) else []):
        if not isinstance(edit, dict):
            continue
        op = str(edit.get("op") or "").strip().lower()
        new_text = str(edit.get("new_text") or "").strip()
        try:
            idx = int(edit.get("paragraph"))
        except (TypeError, ValueError):
            skip(edit, "it did not name a paragraph number")
            continue
        target = by_index.get(idx)
        if op not in ("replace", "insert_after", "delete"):
            skip(edit, "unknown edit type")
        elif target is None:
            skip(edit, f"there is no paragraph {idx}")
        elif len(applied) >= MAX_EDITS:
            skip(edit, f"more than {MAX_EDITS} edits were proposed")
        elif op == "delete":
            problem = ""
            if not allow_delete:
                problem = "lines are only removed when you ask to remove them"
            elif target["locked"]:
                problem = "that paragraph contains a link/field that removing would break"
            elif target["heading"] and not allow_headings:
                problem = "headings and title lines are never removed"
            elif _YEAR_RE.search(target["text"]):
                problem = "that line contains dates, which must never change"
            elif idx in replaced:
                problem = "that paragraph was already edited"
            if problem:
                skip(edit, problem)
            else:
                el = target["paragraph"]._p
                el.getparent().remove(el)
                replaced.add(idx)
                applied.append({"op": "delete", "paragraph": idx, "before": target["text"], "after": ""})
        elif op == "replace":
            problem = ""
            if target["locked"]:
                problem = "that paragraph contains a link/field that editing would break"
            elif target["heading"] and not allow_headings:
                problem = "headings and title lines are never edited"
            elif idx in replaced:
                problem = "that paragraph was already edited"
            elif new_text == target["text"].strip():
                problem = "the text was not changed"
            else:
                problem = edit_problem(target["text"], new_text) or (extra_check(target["text"], new_text, False) if extra_check else "")
            if problem:
                skip(edit, problem)
            elif not _replace_text(target["paragraph"], new_text):
                skip(edit, "that paragraph holds an image")
            else:
                replaced.add(idx)
                applied.append({"op": "replace", "paragraph": idx, "before": target["text"], "after": new_text})
        else:  # insert_after
            problem = ""
            if target["heading"]:
                problem = "a new line can't be added straight after a heading (it would copy the heading's formatting)"
            else:
                problem = edit_problem("", new_text, inserting=True) or (extra_check("", new_text, True) if extra_check else "")
            if problem:
                skip(edit, problem)
            else:
                anchor = last_inserted.get(idx, target["paragraph"]._p)
                last_inserted[idx] = _clone_after(anchor, target["paragraph"], new_text)
                applied.append({"op": "insert_after", "paragraph": idx, "before": target["text"], "after": new_text})

    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue(), applied, skipped
