
import os
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import DigestPreference
# from .worker import run_digest_for_family

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
        return (now_local.weekday() in days) and now_local.hour == hh and now_local.minute == mm
    return False

def tick():
    db: Session = SessionLocal()
    # try:
    #     prefs = db.query(DigestPreference).all()
    #     now_utc = pytz.utc.localize(datetime.utcnow())
    #     for p in prefs:
    #         tz = pytz.timezone(p.timezone or os.getenv("DEFAULT_TIMEZONE","America/Los_Angeles"))
    #         now_local = now_utc.astimezone(tz)
    #         if _should_run_now(p, now_local):
    #             run_digest_for_family(db, p.family_id)
    #     db.commit()
    # finally:
    #     db.close()

def start_scheduler():
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(tick, "interval", seconds=60, id="tick")
    sched.start()
    return sched
