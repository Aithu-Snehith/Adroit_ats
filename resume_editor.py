"""Resume chat ("Edit with AI"): the recruiter says what to change, the AI edits the Word file in place.

The AI sees the resume as numbered paragraphs (docx_editor.numbered_listing) and answers with edits -
replace / insert_after / delete - which docx_editor.apply_edits writes into the SAME .docx, so fonts,
bullets, tables and section order stay as they were. Every edit is checked on its own:
- dates and years-of-experience never change (docx_editor.edit_problem);
- a headline / job-title line changes only when the instruction asks for it (headline, title, role ...),
  and lines are removed only when the instruction asks to remove / shorten something;
- a technology (resume_bot.TECH_KEYWORDS) or a figure ("40%", "15 teams") that is neither in the resume
  nor in the recruiter's instruction is refused - naming it in the instruction is the confirmation
  that the consultant really has it.
The reply lists what changed and what was refused, and why.
"""
import base64
import json
import re
from typing import Dict, List

import application_brief
import docx_editor
import resume_bot

SYSTEM = """You edit a resume exactly as the recruiter instructs. You see the resume as numbered paragraphs.
Change ONLY what the instruction asks; keep the writing style; keep everything else as it is.
Never change dates, years of experience, employers or company names. Change a headline or a job title
only when the instruction asks for it. Never add a technology, tool, certification, project, client or
number that is not already in the resume unless the INSTRUCTION itself names it. If the instruction is
unclear, or would need invented facts, make no edits and ask in "needs_info".
Edit operations: {"op":"replace","paragraph":N,"new_text":"..."} | {"op":"insert_after","paragraph":N,"new_text":"..."}
| {"op":"delete","paragraph":N}. A new bullet goes after an existing bullet of the same section.
Return ONLY JSON: {"edits":[...], "reply":"one or two sentences: what you changed", "needs_info":["..."]}"""

_HEADING_ASK = re.compile(r"\b(head(?:line|ing|er)|title|role|designation|position name)\b", re.I)
_DELETE_ASK = re.compile(r"\b(remove|delete|drop|cut|shorten|shorter|trim|condense|reduce|merge|fewer|max(?:imum)?)\b", re.I)


def _new_tech(text: str, allowed: str) -> List[str]:
    return [t for t in resume_bot.TECH_KEYWORDS if resume_bot._tech_present(t, text) and not resume_bot._tech_present(t, allowed)]


def edit(docx_bytes: bytes, instruction: str, original_text: str = "", jd_text: str = "") -> Dict:
    """{docx_base64, text, applied, skipped, reply, needs_info, changed}. Never raises; when nothing
    can be applied the document comes back unchanged and `reply` says why."""
    instruction = (instruction or "").strip()
    _, paragraphs = docx_editor.load_paragraphs(docx_bytes)
    current_text = "\n".join(p["text"] for p in paragraphs)
    unchanged = {"docx_base64": base64.b64encode(docx_bytes).decode("ascii"), "text": current_text, "applied": [], "skipped": [],
                 "needs_info": [], "changed": False}
    if not instruction:
        return dict(unchanged, reply="Tell me what to change.")
    if not resume_bot.ai_configured():
        return dict(unchanged, reply="The AI isn't set up on the server (no ANTHROPIC_API_KEY or GEMINI_API_KEY) - edit the text directly.")
    jd_part = (f"JOB DESCRIPTION (context only - e.g. for its title or keywords; its requirements are NOT the candidate's "
               f"experience):\n{jd_text[:5000]}\n\n") if (jd_text or "").strip() else ""
    prompt = f"{jd_part}RESUME (numbered paragraphs):\n{docx_editor.numbered_listing(paragraphs)}\n\nINSTRUCTION:\n{instruction}\n"
    try:
        data, model = application_brief._ai(SYSTEM, prompt)
    except Exception as ex:
        return dict(unchanged, reply=f"The AI couldn't answer ({resume_bot._explain_gemini_error(ex)}) - nothing was changed.")
    edits = data.get("edits") if isinstance(data.get("edits"), list) else []
    needs = [str(x) for x in (data.get("needs_info") or []) if str(x).strip()][:5]
    allowed_text = "\n".join([current_text, original_text or "", instruction])

    def extra_check(old, new, inserting):
        added = _new_tech(new, allowed_text)
        if added:
            return f"it adds {', '.join(added)}, which isn't in the resume - name it in your instruction if the consultant really has it"
        figs = application_brief.guard_metrics(new, allowed_text, {})
        if figs:
            return "it adds " + "; ".join(figs)
        return ""

    new_bytes, applied, skipped = docx_editor.apply_edits(
        docx_bytes, edits, extra_check=extra_check,
        allow_headings=bool(_HEADING_ASK.search(instruction)), allow_delete=bool(_DELETE_ASK.search(instruction)))
    if not applied:
        why = "; ".join(f"line {s['paragraph']}: {s['reason']}" for s in skipped[:4])
        msg = "Nothing was changed." + (f" Refused: {why}." if why else "") + (" " + " ".join(needs) if needs else "")
        return dict(unchanged, skipped=skipped, needs_info=needs, reply=msg.strip())
    text = docx_editor.extract_text(new_bytes)
    reply = str(data.get("reply") or "").strip() or f"Made {len(applied)} change{'s' if len(applied) != 1 else ''}."
    if skipped:
        reply += " Not applied: " + "; ".join(f"line {s['paragraph']} ({s['reason']})" for s in skipped[:4]) + "."
    return {"docx_base64": base64.b64encode(new_bytes).decode("ascii"), "text": text, "applied": applied, "skipped": skipped,
            "needs_info": needs, "reply": reply, "changed": True, "model": model}
