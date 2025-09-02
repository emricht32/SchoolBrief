# app/digest_job.py
from datetime import datetime, timezone
from typing import List, Tuple
import os
import pytz

from sqlalchemy.orm import Session
from .models import OneLiner, DigestRun, Family
from .emailer import send_email
from .llm_digest import format_digest_from_oneliners
from .logger import logger

DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")

def _tz(tz_name: str):
    return pytz.timezone(tz_name or DEFAULT_TZ)

from datetime import datetime
from typing import List, Dict

def _filter_future_items(items: List[Dict], today_str: str = None) -> List[Dict]:
    """
    Keep only items where date_string >= today (if date_string exists).
    Items without a date_string are kept.
    """
    if today_str is None:
        today_str = datetime.now().strftime("%Y-%m-%d")

    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    kept = []

    for it in items:
        ds = it.get("date_string")
        if ds:
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                if d >= today:
                    kept.append(it)
            except ValueError:
                # if date_string is malformed, keep item (or skip depending on your preference)
                kept.append(it)
        else:
            # no date_string → keep
            kept.append(it)

    return kept


# ---------- main ----------
def compile_and_send_digest(
    db: Session,
    family_id: int,
    to_emails: List[str],
    cadence: str = "weekly",
) -> Tuple[bool, str]:
    # logger.debug("$")
    fam = db.query(Family).filter_by(id=family_id).first()
    if not fam:
        return False, "Family not found"

    tz_name = (fam.prefs.timezone if fam and fam.prefs else None) or DEFAULT_TZ

    run = DigestRun(family_id=family_id, started_at=datetime.utcnow(), cadence=cadence)
    db.add(run); db.commit(); db.refresh(run)
    # logger.debug("$$")

    try:
        # Grab this family's one-liners (you can narrow to the upcoming/current week if you prefer)
        rows = (
            db.query(OneLiner)
            .filter(OneLiner.family_id == family_id)
            .order_by(OneLiner.date_string.asc().nulls_last(), OneLiner.created_at.asc())
            .all()
        )
        # logger.debug("$$$")

        if not rows:
            run.email_sent = False
            run.error = "No one-liners to include."
            run.ended_at = datetime.utcnow()
            db.add(run); db.commit()
            return False, run.error

        # Prepare compact payload for LLM
        items = []
        for it in rows:
            items.append({
                "one_liner": it.one_liner,
                "date_string": it.date_string,
                "time_string": it.time_string,
                "domain": it.domain
            })
        items = _filter_future_items(items)
        subject, html, text = format_digest_from_oneliners(
            family_display_name=(fam.display_name or ""),
            cadence=cadence,
            tz_name=tz_name,
            items=items,
        )
        # logger.debug("$$$$")

        if not (html and text):
            run.email_sent = False
            run.error = "LLM returned empty content."
            run.ended_at = datetime.utcnow()
            db.add(run); db.commit()
            return False, run.error

        # Email it
        send_email(subject, html, text, to_emails)

        run.email_sent = True
        run.items_found = len(items)
        run.ended_at = datetime.utcnow()
        db.add(run); db.commit()
        return True, "sent"

    except Exception as e:
        run.email_sent = False
        run.error = f"{e}"
        run.ended_at = datetime.utcnow()
        db.add(run); db.commit()
        return False, str(e)
