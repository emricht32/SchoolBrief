# app/summarizer.py
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Tuple
from .logger import logger

from openai import OpenAI

# If you still want LLM phrasing, we’ll keep it, but we’ll feed it already-normalized
# bullet points so it can’t invent dates or links.
CLIENT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

_URL_RE = re.compile(r'https?://\S+')
# _WS_RE = re.compile(r'\s+')

def _fmt_date_iso(iso: str, tz: str = "America/Los_Angeles") -> str:
    logger.debug("")
    # Render YYYY-MM-DD from ISO regardless of snippet contents
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Keep it simple: Month D, YYYY in local TZ if you want; otherwise use UTC date
        # (Simplest: just format in the dt’s timezone)
        return dt.strftime("%B %-d, %Y") if hasattr(dt, "strftime") else iso
    except Exception:
        return iso

# def _clean_snippet(s: str) -> str:
#     # Drop URLs and collapse whitespace so we don’t leak trackers or example.com
#     s = _URL_RE.sub("", s)
#     s = _WS_RE.sub(" ", s).strip()
#     return s

def _group_items(items: List[Dict]) -> Dict[str, List[Dict]]:
    logger.debug("")
    groups = {"deadline": [], "event": [], "reminder": []}
    for it in items:
        typ = it.get("type", "event")
        if typ not in groups:
            typ = "event"
        groups[typ].append(it)
    print("groupts=",groups)
    return groups

# app/summarizer.py (inside your bullets builder)
_WS_RE = re.compile(r'\s+')

def _sender_hint(it: dict) -> str:
    logger.debug("")
    src = it.get("_from","")
    m = re.search(r'<([^>]+)>', src)
    email = (m.group(1) if m else src).strip()
    return email.split('@')[-1] if '@' in email else email

def _title_from(it: dict) -> str:
    logger.debug("")
    subj = (it.get("_subject") or "").strip()
    snip = (it.get("snippet") or "").strip()
    t = subj or snip
    t = _WS_RE.sub(" ", t)
    t = re.sub(r'\b(?:weekly|this week|reminder)\b[:\-\s]*', '', t, flags=re.I).strip()
    return t or "Update"

def _align_year(text: str, iso: str) -> str:
    logger.debug("")
    try:
        import datetime as _dt
        yr = _dt.datetime.fromisoformat(iso).year
        return re.sub(r"\b20\d{2}\b", str(yr), text)
    except Exception:
        return text

def _to_bullets(items: list[dict]) -> list[str]:
    logger.debug("")
    bullets = []
    for it in items:
        if not it.get("dates"):
            continue
        iso = it["dates"][0].get("iso","")
        disp = _fmt_date_iso(iso)
        title = _align_year(_title_from(it), iso)
        src = _sender_hint(it)
        bullets.append(f"{title} — {disp} (from {src})")
    return bullets

# def summarize_html_text(items: List[Dict], brand: Dict) -> Tuple[str, str]:
#     logger.debug("")
#     """
#     Deterministic summary:
#     - Dates always rendered from ISO (anchored) → no '2023' leaks.
#     - Snippets sanitized (no URLs).
#     - No example.com or template links.
#     """
#     product = brand.get("product_name", "SchoolBrief")
#     children = brand.get("children", [])
#     kids_label = ", ".join([c.get("name","") for c in children]) or "All Children"

#     groups = _group_items(items)
#     deadlines = _to_bullets(groups["deadline"])
#     events = _to_bullets(groups["event"])
#     reminders = _to_bullets(groups["reminder"])

#     # If you want a zero-LLM version, just render HTML/TXT below and return.
#     # If you prefer a tiny LLM polish (no new facts), we keep temp=0 and forbid adding links/dates.

#     def _section_html(title: str, bullets: List[str]) -> str:
#         if not bullets:
#             return ""
#         lis = "\n".join([f"<li>{b}</li>" for b in bullets])
#         return f"<h3>{title}</h3>\n<ul>\n{lis}\n</ul>\n"

#     def _section_txt(title: str, bullets: List[str]) -> str:
#         if not bullets:
#             return ""
#         lines = "\n".join([f"- {b}" for b in bullets])
#         return f"{title}\n{lines}\n"

#     # Raw deterministic (safe) body
#     html_body = f"""\
# <h2>{product} — Weekly School Digest</h2>

# <h3>For: {kids_label}</h3>
# {_section_html("Due This Week", deadlines)}
# {_section_html("Events", events)}
# {_section_html("Reminders", reminders)}
# """

#     txt_body = f"""\
# {product} — Weekly School Digest

# For: {kids_label}
# {_section_txt("Due This Week", deadlines)}
# {_section_txt("Events", events)}
# {_section_txt("Reminders", reminders)}
# """
#     return html_body, txt_body
#     # # If everything is empty, provide a friendly message
#     # if not any([deadlines, events, reminders]):
#     #     html_body += "<p>Nothing urgent this week!</p>\n"
#     #     txt_body += "\nNothing urgent this week!\n"

#     # # Optional: a tiny LLM pass to tidy wording ONLY (no new facts).
#     # # We lock it down by telling it not to invent dates or links.
#     # SYSTEM = (
#     #     "You rewrite for clarity only. Do not invent or add any facts, dates, links, or examples. "
#     #     "Never include example.com. Use exactly the provided bullet points and wording, only light edits."
#     # )

#     # prompt = (
#     #     "Rewrite the following HTML for clarity and parent-friendliness. "
#     #     "Do NOT add links or dates. Do NOT change any dates.\n\n"
#     #     f"HTML:\n{html_body}\n\n"
#     #     "Then rewrite the following TEXT version similarly.\n\n"
#     #     f"TEXT:\n{txt_body}"
#     # )
# #
#     # try:
#     #     resp = CLIENT.chat.completions.create(
#     #         model="gpt-4o-mini-2024-07-18",
#     #         temperature=0,           # no creativity; no invention
#     #         messages=[
#     #             {"role": "system", "content": SYSTEM},
#     #             {"role": "user", "content": prompt},
#     #         ],
#     #     )
#     #     out = resp.choices[0].message.content or ""
#     #     # Very simple split back out; if model merges, fall back to deterministic.
#     #     if "<html" in out or "<h2" in out:
#     #         # crude split: take first html block then text code fence if present
#     #         html_out = out
#     #         txt_out = txt_body
#     #     else:
#     #         html_out = html_body
#     #         txt_out = txt_body
#     #     return html_out, txt_out
#     # except Exception:
#     #     # If OpenAI fails, fall back to our deterministic render
#     #     return html_body, txt_body
