
import os
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import DigestPreference
from .compile_job import compile_and_send_digest
from .ingest_job import process_recent_emails_saving_to_points, process_forwarded_emails_and_update_domains, collect_recent_emails

# scheduler.py (only showing the changed parts)
import os
import logging
from datetime import datetime
import pytz
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import DigestPreference, User, Family
from .digest_runner import run_digest_once  # ← NEW
from .logger import logger

DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")

def run_digest_for_family(db: Session, family_id: int):
    logger.debug(f"[SCHEDULER] run_digest_for_family.family_id={family_id}")
    pref = db.query(DigestPreference).filter_by(family_id=family_id).first()
    if not pref:
        return False, "No DigestPreference found for family"

    try:
        owner_email = getattr(pref.family.owner, "email", None) if pref.family and pref.family.owner else None
        sent, msg, metrics = run_digest_once(db, family_id, pref, user_email_fallback=owner_email)
        logger.info(f"[family_id={family_id}] sent={sent} msg={msg} metrics={metrics}")
        return sent, msg
    except Exception as e:
        logger.exception(f"[family_id={family_id}] run_digest_for_family failed: {e}")
        return False, f"Exception: {e}"

def _should_run_now(pref, now_local):
    try:
        hh, mm = map(int, (pref.send_time_local or "07:00").split(":"))
    except:
        hh, mm = 7, 0
    if pref.cadence == "daily":
        return now_local.hour == hh and now_local.minute == mm
    if pref.cadence == "weekly":
        days = [int(x) for x in (pref.days_of_week or "").split(",") if x.strip().isdigit()]
        if not days: days = [6]  # default Sunday
        return (now_local.weekday() in days) # and now_local.hour == hh and now_local.minute == mm
    return False

def tick(force=False):
    """Run scheduling pass; return count of families whose digest was triggered."""
    logger.debug(f"[SCHEDULER] tick(force={force})")
    db: Session = SessionLocal()
    triggered = 0
    try:
        prefs = db.query(DigestPreference).all()
        now_utc = pytz.utc.localize(datetime.utcnow())
        for p in prefs:
            tz = pytz.timezone(p.timezone or os.getenv("DEFAULT_TIMEZONE","America/Los_Angeles"))
            now_local = now_utc.astimezone(tz)
            if _should_run_now(p, now_local) or force:
                run_digest_for_family(db, p.family_id)
                triggered += 1
        db.commit()
        return triggered
    finally:
        db.close()

def start_scheduler():
    sched = BackgroundScheduler(timezone="UTC")
    # Run at :00 and :30 each hour
    sched.add_job(tick, 'cron', minute='0,30', id='tick')
    sched.start()
    return sched
