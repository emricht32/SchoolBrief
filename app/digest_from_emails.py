# app/digest_from_emails.py
import json, re
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any
import pytz
from openai import OpenAI

from .models import DigestRun, Family
from .emailer import send_email

client = OpenAI()

EMAILS_DIGEST_PROMPT = """
You are generating a parent-friendly weekly email digest directly from raw emails.

You will be given:
- run_date (YYYY-MM-DD local)
- timezone (IANA string)
- emails: an array of objects with keys:
  - title: string (email subject)
  - date: string or null (best date for the item; can be ISO, natural language, or missing)
  - text: string (cleaned email body text)
  - sender_domain: string (e.g., "parentsquare.com")

GOALS
1) Produce a concise, accurate, skimmable email for parents.
2) Cover exactly 7 days starting on run_date (inclusive). Place items strictly after that window into an "Upcoming" section. Omit items before run_date.
3) De-dupe near-identical items; keep the most complete details.
4) Correct weekday/date mismatches (silently) and apply the provided timezone consistently.
5) Group into (only include a section if it has items):
   - Events
   - Homework & Tests
   - Activities & Clubs
   - Reminders
   - Other / Misc
   After those, include:
   - Upcoming
   - Reminders (Undated/Unclear) — only if necessary.

TIME FORMAT
- Use 12-hour times with AM/PM and friendly patterns like: "Thu, Sep 4 • 12:00–1:00 PM".
- If the item is date-only (no explicit time), show just the date (no default time).

STUDENT NAMES & PER-CHILD ITEMS
- If a line clearly refers to a specific student (e.g., "Aria:", "Chance:"), nest as a sub-bullet under the relevant section item.

OUTPUT FORMAT (STRICT)
Return ONLY a JSON object with keys: "subject", "html", and "text".
- "subject": a concise email subject line like "Weekly School Digest: Sep 1–Sep 7"
- "html": the full HTML body (with headings and bullet lists)
- "text": a plaintext version of the same content

NO extra commentary or keys. NO markdown — HTML for the body, and plaintext separately.
"""

def _extract_json_block(s: str) -> Dict[str, Any]:
    """
    Try to parse JSON directly, or extract the largest {...} block.
    """
    s = s.strip()
    # 1) direct
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2) find a JSON object block
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    raise ValueError("LLM did not return valid JSON")

def _run_date_iso(tz_name: str) -> str:
    tz = pytz.timezone(tz_name or "America/Los_Angeles")
    return datetime.now(tz).date().isoformat()

def _payload_for_llm(emails: List[Dict[str, Any]], tz_name: str) -> Dict[str, Any]:
    return {
        "run_date": _run_date_iso(tz_name),
        "timezone": tz_name or "America/Los_Angeles",
        "emails": emails,
    }

def _call_llm_for_digest(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Calls Chat Completions and returns (subject, html, text).
    """
    resp = client.chat.completions.create(
        # model="gpt-5-mini",
        model="gpt-4.1-mini",
        temperature=0.2,
        messages=[
            {"role": "system", "content": EMAILS_DIGEST_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ],
    )
    content = resp.choices[0].message.content
    obj = _extract_json_block(content)

    subject = (obj.get("subject") or "").strip()
    html = (obj.get("html") or "").strip()
    text = (obj.get("text") or "").strip()
    if not subject or not html or not text:
        raise ValueError("LLM returned empty or incomplete JSON (need subject, html, text).")
    return subject, html, text

def compile_and_send_digest_from_emails(
    db,
    family_id: int,
    to_emails: List[str],
    cadence: str,
    emails: List[Dict[str, Any]],
    tz_name: str,
) -> Tuple[bool, str]:
    """
    Build a 7-day weekly digest straight from raw emails via LLM.

    emails: List[ { "title": str, "date": str|None, "text": str, "sender_domain": str } ]
    """
    fam = db.query(Family).filter_by(id=family_id).first()
    if not fam:
        return False, "Family not found"

    run = DigestRun(family_id=family_id, started_at=datetime.utcnow(), cadence=cadence)
    db.add(run); db.commit(); db.refresh(run)

    try:
        if not emails:
            run.email_sent = False
            run.error = "No emails in the selected window."
            run.ended_at = datetime.utcnow()
            db.add(run); db.commit()
            return False, run.error

        payload = _payload_for_llm(emails, tz_name)
        subject, html, text = _call_llm_for_digest(payload)

        send_email(subject, html, text, to_emails)

        run.email_sent = True
        run.items_found = len(emails)  # count of source emails summarized
        run.ended_at = datetime.utcnow()
        db.add(run); db.commit()
        return True, "sent"

    except Exception as e:
        run.email_sent = False
        run.error = f"{e}"
        run.ended_at = datetime.utcnow()
        db.add(run); db.commit()
        return False, str(e)
