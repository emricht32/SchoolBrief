# app/llm_digest.py
import json
from typing import List, Dict, Tuple
from openai import OpenAI
from datetime import date
from .logger import logger

_client = OpenAI()

def _safe_json_loads(s: str) -> Dict:
    try:
        return json.loads(s)
    except Exception:
        # Try to pull out a fenced JSON block if present
        import re
        m = re.search(r"\{.*\}\s*$", s, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        raise

def format_digest_from_oneliners(
    family_display_name: str,
    cadence: str,
    tz_name: str,
    items: List[Dict],
) -> Tuple[str, str, str]:
    logger.debug("")
    """
    items: [{ "one_liner": str, "date_string": str|None, "time_string": str|None, "domain": str|None }]
    Returns: (subject, html, text)
    """
    # Compose a single user prompt with instructions and strict JSON requirement.
    # - `{{run_date}}` → the local date when the script runs (ISO: `YYYY-MM-DD`).  
    # If not provided, derive from the system clock.  
    # - `{{timezone}}` → IANA time zone (default: `America/Los_Angeles`).  
    # - `{{one_liners}}` → array of strings.
    run_date = date.today().isoformat()
    user_prompt = {
        "role": "user",
        "content": (
            "Prepare a weekly digest for the family below.\n\n"
            f"Family: {family_display_name or ''}\n"
            f"Cadence: {cadence}\n"
            f"timezone: {tz_name}\n"
            f"one_liners: {json.dumps(items, ensure_ascii=False)}\n"
            f"run_date: {run_date}"
        ),
    }
    from .prompt import WEEKLY_DIGEST_PROMPT, WEEKLY_DIGEST_PROMPT2
    resp = _client.chat.completions.create(
        # model="gpt-5-mini",
        model="gpt-4.1-mini",
        temperature=0.2,
        messages=[
            {"role": "system", "content": WEEKLY_DIGEST_PROMPT2},
            user_prompt,
        ],
        
    )

    out = resp.choices[0].message.content or ""
    obj = _safe_json_loads(out)

    subject = (obj.get("subject") or "SchoolBrief — Weekly School Digest").strip()
    html = (obj.get("html") or "").strip()
    text = (obj.get("text") or "").strip()
    logger.debug("html=%s", html)
    logger.debug("text=%s", text)

    return subject, html, text
