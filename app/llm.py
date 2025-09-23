# app/llm.py
import json
from typing import List, Dict
from openai import OpenAI
from .logger import logger
from .errors import build_error_notice
from datetime import datetime
from zoneinfo import ZoneInfo  # Python 3.9+
import os, httpx
from functools import lru_cache

@lru_cache(maxsize=1)

def get_openai():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    # Normalize base URL
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        base_url = base_url.strip()
        # Accept either https://api.openai.com or https://api.openai.com/v1
        if base_url.endswith("/"):
            base_url = base_url[:-1]
        if not base_url.startswith("http"):
            # If someone set 'api.openai.com' without scheme, fix it
            base_url = f"https://{base_url}"
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
    else:
        base_url = "https://api.openai.com/v1"

    logger.debug(f"[LLM] Using OpenAI base_url={base_url}")

    # Optional: quick connectivity sanity check (1s timeout)
    try:
        with httpx.Client(timeout=1.0) as hx:
            hx.get(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
    except Exception as e:
        logger.exception(f"[LLM] Connectivity/base URL check failed for {base_url}: {e}")
        notice = build_error_notice(e, {"op": "openai.chat"})
        logger.error(f"[{notice.code}] {notice.debug} (ref={notice.support_id})")
        # Raise or return a sentinel; your caller decides how to flash/continue
        raise RuntimeError(notice.flash_text())

    return OpenAI(api_key=api_key, base_url=base_url)

client = get_openai()  # uses OPENAI_API_KEY

_SYSTEM = (
    "You extract concise, parent-friendly action items from school/activity emails. "
    "Return compact points. Include a date/time ONLY if it exists explicitly in the email."
)

_USER_TEMPLATE = """\
You are given an email subject and plain-text OR html body. 
Identify at most 6 actionable points relevant to K-12 families(e.g., homework, due dates, events, practices, 
rehearsals, closures, forms, fees). These actionable points should only be the most important items relevant 
to a Parent with a child in school. There is no requirement to create 6 points. Fine print and small details 
need not be included. Dates with times should be prioritized, then just dates or days of the week, and finally 
items without dates. Ignore promos and generic ads. Do not include the date and/or time in the `one_liner` text. 
Only include it in the `date_string` or `time_string.

**Runtime context (variables you may be given):**
- The local date when the script runs is `run_date={run_date}`. If run_date is None, use the system clock
to determine the date. Nod date should have a year before 2025.  

STRICT RULES:
- Output STRICT JSON:
  {{
    "points": [
      {{
        "one_liner": "string, <= 140 chars, clear, specific, data and time NOT INCLUDED",
        "date_string": "One of: '' (empty, no date); 'YYYY-MM-DD' (date only)",
        "time_string": "One of: '' (empty, no time); string, 12 hour clock, local time. hh:mm AM/PM",
        "from_domain": "{domain}"
      }}
    ]
  }}
- Do NOT invent times. If the email has a date but no time, use DATE-ONLY (e.g., "2025-09-02").
- Do NOT invent years. All years should be expected to be the same year as `run_date`
- Use local timezone for any explicit times: {local_tz}.
- Merge duplicates inside this email.

KEEP POINT ONLY IF THE ITEM IS:
- Directly tied to school events, classes, tutoring, or clubs.
- Provides safety, mental health, or student well-being resources.
- Covers school logistics (pictures, back-to-school night, holiday weekends, etc.).
- Specifically about academic/club opportunities within school (math festival, robotics, Science Olympiad, sTEAM Magazine, etc.).
- After school clubs that include dates and times

EXCLUDE IF THE POINT IS:
- General community or enrichment opportunities
- Mentions community flyers, art wall, Bloom Institute, Renewable Energy Challenge, Cupertino FC Youth Soccer, American Computer Science League, Arts & Culture Commission, etc.
- Adult/parent-facing only (not directly about a student’s schooling experience)
  * Example: Submit online public comments at the Board Meeting.
  * Example: Volunteer to teach band and guard.
- Broad seasonal/awareness events
- Mentions Emergency Preparedness Month, studio calendar, or other things not tied to the child’s schoolwork or immediate schedule.
- Optional extracurriculars not run through the school
- Generic classes, camps, enrichment courses, or contests that don’t directly tie to the student’s classroom, school requirements, or urgent needs.

Subject: {subject}

Body:
{body}
"""

def _coerce_points(obj) -> List[Dict]:
    logger.debug("")
    if not isinstance(obj, dict):
        return []
    pts = obj.get("points", [])
    if not isinstance(pts, list):
        return []
    out = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        one = (p.get("one_liner") or "").strip()
        when_iso = (p.get("when_iso") or "").strip()
        date = (p.get("date_string") or "").strip()
        time = (p.get("time_string") or "").strip()
        if one:
            out.append({
                "one_liner": one[:200],
                "when_iso": when_iso,
                "date_string": date,
                "time_string": time
            })
    return out

def summarize_email_to_points(subject: str, body_text: str, domain: str, local_tz: str = "America/Los_Angeles") -> List[Dict]:
    # run_date = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    run_date = datetime.now().date().isoformat()

    prompt = _USER_TEMPLATE.format(subject=subject or "", body=body_text or "", local_tz=local_tz, run_date=run_date or "", domain=domain)
    try:
        logger.debug("calling OpenAI...")
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            timeout=30,
        )
        logger.debug("resp OK")
    except Exception as e:
        notice = build_error_notice(e, {"op": "openai.chat"})
        logger.error(f"[{notice.code}] {notice.debug} (ref={notice.support_id})")
        # Raise or return a sentinel; your caller decides how to flash/continue
        raise RuntimeError(notice.flash_text())


    text = resp.choices[0].message.content or ""
    raw = text.strip()
    logger.debug("raw=%s",raw)
    if raw.startswith("```"):
        parts = raw.split("```", 2)
        raw = parts[1] if len(parts) > 1 else raw
        raw = raw.lstrip("json").lstrip()

    try:
        data = json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(raw[start:end+1])
            except Exception:
                return []
        else:
            return []

    return _coerce_points(data)
