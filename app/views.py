
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from .db import SessionLocal
# views.py (top imports)
from .models import (
    User, Family, Child, DigestRun,
    Subscription, ReferralCode, OneLiner, ProcessedEmail
)
from .stripe_sync import ensure_subscription_items
from urllib.parse import quote_plus
from .ingest_job import collect_recent_emails, process_recent_emails_saving_to_points
from .compile_job import compile_and_send_digest
from .utils import csv_to_list
from typing import Dict, List, Tuple
from sqlalchemy import or_, and_
from .logger import logger
from .digest_from_emails import compile_and_send_digest_from_emails


router = APIRouter()
templates = Jinja2Templates(directory="templates")

import pytz
# from datetime import datetime

def _week_bounds_now(local_tz_name: str, cadence: str) -> Tuple[datetime, datetime]:
    """Return [start_utc, end_utc) based on today in local tz."""
    tz = pytz.timezone(local_tz_name or "America/Los_Angeles")
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if (cadence or "weekly").lower() == "weekly":
        end_local = start_local + timedelta(days=7)
    else:
        end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

def _localtime(dt, tz_name: str):
    if not dt:
        return ""
    tz = pytz.timezone(tz_name or "America/Los_Angeles")
    if dt.tzinfo is None:
        # our datetimes are stored as naive UTC
        dt = pytz.utc.localize(dt)
    return dt.astimezone(tz).strftime("%Y-%m-%d %I:%M %p")  # e.g., 2025-08-26 07:05 PM

templates.env.filters["localtime"] = _localtime


def _db():
    return SessionLocal()

def _current_user(db: Session, request: Request):
    email = request.session.get("user_email")
    if not email:
        return None
    return db.query(User).filter_by(email=email).first()

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    flash = request.query_params.get("flash")
    db = _db()
    try:
        user = _current_user(db, request)
        return templates.TemplateResponse("index.html", {"request": request, "user": user, "flash": flash})
    finally:
        db.close()

@router.get("/app", response_class=HTMLResponse)
def dashboard(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        pref = fam.prefs
        sub = (
            db.query(Subscription)
            .filter_by(family_id=fam.id)
            .order_by(Subscription.id.desc())
            .first()
        )
        recent_runs = (
            db.query(DigestRun)
            .filter_by(family_id=fam.id)
            .order_by(DigestRun.started_at.desc())
            .limit(20)
            .all()
        )
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "family": fam,
                "pref": pref,
                "sub": sub,
                "recent_runs": recent_runs,  # <-- IMPORTANT: match template
            },
        )
    finally:
        db.close()


@router.post("/app/run-now")
def run_now(request: Request):
    db = _db()
    try:
        logger.debug("")
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in", status_code=303)

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=" + quote_plus("No family configured"), status_code=303)

        pref = fam.prefs
        allowed_domains = [d.strip().lstrip("@").lower()
                           for d in (pref.school_domains or "").split(",") if d.strip()]
        to_emails = csv_to_list(pref.to_addresses) or [user.email]
        cadence = (pref.cadence or "weekly").lower()
        tz = pref.timezone or os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
        emails = collect_recent_emails(
                        db=db,
                        family_id=fam.id,
                        allowed_domains=allowed_domains,
                        days_back=7
                    )
        if True:
            # Job A: ingest last 7 days & create OneLiner rows
        #     process_recent_emails_saving_to_points(
        # db: Session,
        # family_id: int,
        # emails: Dict, 
        # local_tz: str = "America/Los_Angeles",
            processed_count, points_created = process_recent_emails_saving_to_points(
                db,
                fam.id,
                emails,
                local_tz=tz
                )
            
            # Job B: compile & send a digest from those one-liners
            sent, msg = compile_and_send_digest(
                db=db,
                family_id=fam.id,
                to_emails=to_emails,
                cadence=cadence,
            )
            
            if sent:
                flash = f"Digest sent — processed {processed_count} email(s), added {points_created} item(s)."
            else:
                flash = f"Digest not sent: {msg} — processed {processed_count} email(s), added {points_created} item(s)."

            return RedirectResponse("/app?flash=" + quote_plus(flash), status_code=303)
        else:
            sent, msg = compile_and_send_digest_from_emails(
                                                    db=db,
                                                    family_id=fam.id,
                                                    to_emails=to_emails,
                                                    cadence=cadence,
                                                    emails=emails,       # ← list of {title, date, text, sender_domain}
                                                    tz_name=tz,
                                                )

            flash = "Digest sent" if sent else f"Digest not sent: {msg}"
            return RedirectResponse("/app?flash=" + quote_plus(flash), status_code=303)

    except Exception as e:
        return RedirectResponse("/app?flash=" + quote_plus(f"Digest not sent: {e}"), status_code=303)
    finally:
        db.close()



@router.get("/settings/family", response_class=HTMLResponse)
def settings_family(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        pref = fam.prefs
        kids = db.query(Child).filter_by(family_id=fam.id).all()
        rc = db.query(ReferralCode).filter_by(family_id=fam.id).first()
        return templates.TemplateResponse("settings_family.html", {
            "request": request, "user": user, "family": fam, "children": kids, "pref": pref, "default_tz": os.getenv("DEFAULT_TIMEZONE","America/Los_Angeles"), "referral_code": rc
        })
    finally:
        db.close()

@router.post("/settings/family")
async def settings_family_post(
    request: Request,
    display_name: str = Form(""),
    recipients: str = Form(""),
    school_domains: str = Form(""),
    include_keywords: str = Form(""),
    cadence: str = Form("weekly"),
    send_time_local: str = Form("07:00"),
    timezone: str = Form("America/Los_Angeles"),
    days_of_week: str = Form(""),
    child_name_new: str = Form(None),
    child_grade_new: str = Form(None),
    child_school_new: str = Form(None),
):
    form = await request.form()
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        fam.display_name = display_name or fam.display_name
        pref = fam.prefs
        pref.to_addresses = recipients
        pref.school_domains = school_domains
        pref.include_keywords = include_keywords
        pref.cadence = cadence
        pref.send_time_local = send_time_local
        pref.timezone = timezone
        pref.days_of_week = days_of_week
        db.add(fam); db.add(pref)

        kids = db.query(Child).filter_by(family_id=fam.id).order_by(Child.id.asc()).all()
        for idx, kid in enumerate(kids):
            name = form.get(f"child_name_{idx}")
            if name is not None:
                kid.name = name
                kid.grade = form.get(f"child_grade_{idx}", "")
                kid.school_name = form.get(f"child_school_{idx}", "")
                db.add(kid)

        if child_name_new:
            db.add(Child(family_id=fam.id, name=child_name_new.strip(), grade=(child_grade_new or "").strip(), school_name=(child_school_new or "").strip()))

        # Update add-on quantity in Stripe (if subscribed)
        sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
        if sub and sub.stripe_subscription_id:
            try:
                ensure_subscription_items(sub, pref)
            except Exception:
                pass

        db.commit()
        return RedirectResponse("/settings/family?flash=Saved", status_code=303)
    finally:
        db.close()

# views.py (add this new route)
@router.post("/app/clear-db")
def clear_db(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in", status_code=303)

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=No+family+configured", status_code=303)

        # Delete only this family's data
        ol_q = db.query(OneLiner).filter(OneLiner.family_id == fam.id)
        pe_q = db.query(ProcessedEmail).filter(ProcessedEmail.family_id == fam.id)

        one_liners_deleted = ol_q.delete(synchronize_session=False)
        processed_deleted = pe_q.delete(synchronize_session=False)

        db.commit()
        flash = f"Cleared {one_liners_deleted} one-liner(s) and {processed_deleted} processed email(s)."
        return RedirectResponse(f"/app?flash={quote_plus(flash)}", status_code=303)
    except Exception as e:
        db.rollback()
        return RedirectResponse("/app?flash=" + quote_plus(f"Clear failed: {e}"), status_code=303)
    finally:
        db.close()

@router.get("/data", response_class=HTMLResponse)
def data_view(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=No+family+configured")

        # Pull latest processed emails for this family
        emails = (
            db.query(
                # minimal fields we’ll render
                DigestRun.id.label("dummy"),  # placeholder, not used; keeps SQLA happy in some configs
            )
        )
        # Simpler approach: two queries, then group in Python.

        processed = (
            db.query(
                # processed_emails columns
                # id omitted for UI
                # we’ll sort newest first
                # NOTE: using model classes directly:
                # ProcessedEmail has: family_id, gmail_msg_id, content_hash, subject, processed_at
                ProcessedEmail.gmail_msg_id,
                ProcessedEmail.subject,
                ProcessedEmail.processed_at,
                ProcessedEmail.content_hash,
            )
            .filter(ProcessedEmail.family_id == fam.id)
            .order_by(ProcessedEmail.processed_at.desc())
            .limit(200)
            .all()
        )

        # Load one-liners for those gmail_msg_ids in one go
        msg_ids = [row.gmail_msg_id for row in processed if row.gmail_msg_id]
        one_liners_map: Dict[str, List[OneLiner]] = {mid: [] for mid in msg_ids}

        if msg_ids:
            ol_rows = (
                db.query(OneLiner)
                .filter(OneLiner.family_id == fam.id)
                .filter(OneLiner.source_msg_id.in_(msg_ids))
                .order_by(OneLiner.created_at.asc())
                .all()
            )
            for ol in ol_rows:
                one_liners_map.setdefault(ol.source_msg_id, []).append(ol)

        # Build view model
        items = []
        for row in processed:
            items.append({
                "gmail_msg_id": row.gmail_msg_id,
                "subject": row.subject or "(no subject)",
                "processed_at": row.processed_at,
                "content_hash": row.content_hash,
                "one_liners": one_liners_map.get(row.gmail_msg_id, []),
            })

        # user’s timezone for display
        pref = fam.prefs
        tz_name = (pref.timezone if pref and pref.timezone else os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"))

        return templates.TemplateResponse("data.html", {
            "request": request,
            "user": user,
            "family": fam,
            "items": items,
            "tz_name": tz_name,
        })
    finally:
        db.close()

# app/views.py (add)
@router.get("/data/preview", response_class=HTMLResponse)
def data_preview(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        pref = fam.prefs
        start_utc, end_utc = _week_bounds_now(pref.timezone or "America/Los_Angeles", pref.cadence or "weekly")
        rows = (
            db.query(OneLiner)
            .filter(OneLiner.family_id == fam.id)
            .filter(
                or_(
                    and_(OneLiner.when_ts != None, OneLiner.when_ts >= start_utc, OneLiner.when_ts < end_utc),
                    and_(OneLiner.when_ts == None, OneLiner.created_at >= start_utc, OneLiner.created_at < end_utc),
                )
            )
            .order_by(OneLiner.when_ts.is_(None), OneLiner.when_ts, OneLiner.created_at.desc())
            .all()
        )
        return templates.TemplateResponse("data_preview.html", {
            "request": request, "rows": rows, "pref": pref, "start": start_utc, "end": end_utc
        })
    finally:
        db.close()

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .models import ActivitySource, Family
from .db import SessionLocal

# ... your existing router/templating ...

@router.get("/settings/sources")
def sources_list(request: Request):
    db = SessionLocal()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        rows = db.query(ActivitySource).filter_by(family_id=fam.id).order_by(ActivitySource.id.desc()).all()
        return templates.TemplateResponse("sources.html", {"request": request, "user": user, "family": fam, "rows": rows})
    finally:
        db.close()

@router.post("/settings/sources/add")
async def sources_add(
    request: Request,
    name: str = Form(...),
    category: str = Form("school"),
    domains_csv: str = Form(""),
    keywords_csv: str = Form(""),
    child_name: str = Form(""),
):
    db = SessionLocal()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        db.add(ActivitySource(
            family_id=fam.id,
            name=name.strip(),
            category=category.strip(),
            domains_csv=domains_csv.strip(),
            keywords_csv=keywords_csv.strip(),
            child_name=(child_name or "").strip() or None,
        ))
        db.commit()
        return RedirectResponse("/settings/sources?flash=Added", status_code=303)
    finally:
        db.close()

@router.post("/settings/sources/delete")
async def sources_delete(request: Request, id: int = Form(...)):
    db = SessionLocal()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in")
        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        row = db.query(ActivitySource).filter_by(family_id=fam.id, id=id).first()
        if row:
            db.delete(row); db.commit()
        return RedirectResponse("/settings/sources?flash=Deleted", status_code=303)
    finally:
        db.close()
