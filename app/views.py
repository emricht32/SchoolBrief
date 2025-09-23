# views.py (top imports)
import email
import re

from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .models import Family
from .db import SessionLocal
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import (
    User, Family, Child, DigestRun, DigestPreference,
    Subscription, ReferralCode, OneLiner, ProcessedEmail
)
from .stripe_sync import ensure_subscription_items
from urllib.parse import quote_plus
from .ingest_job import (
    collect_recent_emails, 
    process_recent_emails_saving_to_points,
    process_forwarded_emails_and_update_domains
)
from .compile_job import compile_and_send_digest
from .utils import csv_to_list
from typing import Dict, List, Tuple
from sqlalchemy import or_, and_
from .logger import logger
from .digest_from_emails import compile_and_send_digest_from_emails
import pytz
from .digest_runner import run_digest_once  # ← NEW


router = APIRouter()
templates = Jinja2Templates(directory="templates")

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


# @router.post("/app/run-now")
# def run_now(request: Request):
#     db = _db()
#     try:
#         from .errors import build_error_notice
#         # print("ONE")
#         user = _current_user(db, request)
#         if not user:
#             return RedirectResponse("/?flash=Please+sign+in", status_code=303)

#         fam = db.query(Family).filter_by(owner_user_id=user.id).first()
#         if not fam:
#             return RedirectResponse(
#                 "/app?flash=" + quote_plus("No family configured"), status_code=303
#             )
#         # print("TWO")
#         pref = fam.prefs
#         allowed_domains = [
#             d.strip().lstrip("@").lower()
#             for d in (pref.school_domains or "").split(",")
#             if d.strip()
#         ]
#          # Job A: process forwarded emails
#         processed_emails = process_forwarded_emails_and_update_domains(db)
#         logger.info(f"Processed forwarded emails: {processed_emails}")
        
#         to_emails = csv_to_list(pref.to_addresses) or [user.email]
#         cadence = (pref.cadence or "weekly").lower()
#         tz = pref.timezone or os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
#         # print("THREE")
#         emails = collect_recent_emails(
#             db=db,
#             family_id=fam.id,
#             allowed_domains=allowed_domains,
#             days_back=7,
#         )
#         if True:
#             # Job A: ingest last 7 days & create OneLiner rows
#             processed_count, points_created = process_recent_emails_saving_to_points(
#                 db,
#                 fam.id,
#                 emails,
#                 local_tz=tz,
#             )
#             # print("processed_count=",processed_count)
#             # print("points_created=",points_created)
#             # Job B: compile & send a digest from those one-liners
#             sent, msg = compile_and_send_digest(
#                 db=db,
#                 family_id=fam.id,
#                 to_emails=to_emails,
#                 cadence=cadence,
#             )

#             if sent:
#                 flash = (
#                     f"Digest sent — processed {processed_count} email(s), "
#                     f"added {points_created} item(s)."
#                 )
#             else:
#                 flash = (
#                     f"Digest not sent: {msg} — processed {processed_count} "
#                     f"email(s), added {points_created} item(s)."
#                 )

#             return RedirectResponse("/app?flash=" + quote_plus(flash), status_code=303)

#         else:
#             sent, msg = compile_and_send_digest_from_emails(
#                 db=db,
#                 family_id=fam.id,
#                 to_emails=to_emails,
#                 cadence=cadence,
#                 emails=emails,  # ← list of {title, date, text, sender_domain}
#                 tz_name=tz,
#             )

#             flash = "Digest sent" if sent else f"Digest not sent: {msg}"
#             return RedirectResponse("/app?flash=" + quote_plus(flash), status_code=303)

#     except Exception as e:
#         notice = build_error_notice(e, {"op": "run-now"})
#         logger.error(f"[{notice.code}] {notice.debug} (ref={notice.support_id})")
#         return RedirectResponse("/app?flash=" + quote_plus(notice.flash_text()), status_code=303)
#     finally:
#         db.close()
# views.py (imports)

@router.post("/app/run-now")
def run_now(request: Request):
    db = _db()
    try:
        from .errors import build_error_notice
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in", status_code=303)

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=" + quote_plus("No family configured"), status_code=303)

        pref = fam.prefs
        if not pref:
            return RedirectResponse("/app?flash=" + quote_plus("No preferences configured"), status_code=303)

        # Single call does the whole pipeline
        sent, msg, metrics = run_digest_once(
            db=db,
            family_id=fam.id,
            pref=pref,
            user_email_fallback=user.email,  # keeps your original fallback
            days_back=7,
        )

        if sent:
            flash = (
                f"Digest sent — processed {metrics['processed_count']} email(s), "
                f"added {metrics['points_created']} item(s)."
            )
        else:
            flash = (
                f"Digest not sent: {msg} — processed {metrics['processed_count']} "
                f"email(s), added {metrics['points_created']} item(s)."
            )

        return RedirectResponse("/app?flash=" + quote_plus(flash), status_code=303)

    except Exception as e:
        notice = build_error_notice(e, {"op": "run-now"})
        logger.error(f"[{notice.code}] {notice.debug} (ref={notice.support_id})")
        return RedirectResponse("/app?flash=" + quote_plus(notice.flash_text()), status_code=303)
    finally:
        db.close()


@router.get("/app/settings")
def settings_get(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in", status_code=303)

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=No+family+configured", status_code=303)

        pref = fam.prefs or DigestPreference(
            family_id=fam.id,
            cadence="weekly",
            send_time_local="07:00",
            timezone=os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
        )
        children = db.query(Child).filter_by(family_id=fam.id).order_by(Child.id.asc()).all()
        referral_code = db.query(ReferralCode).filter_by(family_id=fam.id).first()

        # If you already pass global domain suggestions, replace this with your real query.
        domain_suggestions = []  # e.g., ['parentsquare.com','schoology.com']

        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "user": user,
                "family": fam,
                "pref": pref,
                "children": children,
                "referral_code": referral_code,
                "default_tz": os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
                "domain_suggestions": domain_suggestions,
                "welcome": request.query_params.get("welcome") == "1",
            },
        )
    finally:
        db.close()


@router.post("/app/settings")
async def settings_post(request: Request):
    db = _db()
    try:
        user = _current_user(db, request)
        if not user:
            return RedirectResponse("/?flash=Please+sign+in", status_code=303)

        fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        if not fam:
            return RedirectResponse("/app?flash=No+family+configured", status_code=303)

        form = await request.form()

        # ---- Family display name ----
        fam.display_name = (form.get("display_name") or "").strip() or fam.display_name
        db.add(fam)

        # ---- Preferences (create if missing) ----
        pref = fam.prefs or DigestPreference(
            family_id=fam.id,
            cadence="weekly",
            send_time_local="07:00",
            timezone=os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
        )

        # recipients
        pref.to_addresses = (form.get("recipients") or "").strip()

        # detail level (full|focused) — default to full
        dl = (form.get("detail_level") or "full").strip().lower()
        if dl not in ("full", "focused"):
            dl = "full"
        pref.detail_level = dl

        # cadence
        cad = (form.get("cadence") or "weekly").strip().lower()
        if cad not in ("daily", "weekly"):
            cad = "weekly"
        pref.cadence = cad

        # days of week (hidden CSV kept in sync by JS)
        pref.days_of_week = (form.get("days_of_week") or "").strip()

        # time & tz
        pref.send_time_local = (form.get("send_time_local") or "07:00").strip()
        pref.timezone = (form.get("timezone") or os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")).strip()

        # domains — normalize: lowercase, strip @, dedupe, comma-join
        raw_domains = (form.get("school_domains") or "")
        dom_list = [
            d.lstrip("@").strip().lower()
            for d in raw_domains.split(",")
            if d.strip()
        ]
        # unique & stable order (alphabetical)
        dom_list = sorted(set(dom_list))
        pref.school_domains = ", ".join(dom_list)

        # include keywords
        pref.include_keywords = (form.get("include_keywords") or "").strip()

        db.add(pref)
        db.commit()

        # ---- Children ----
        # Existing children are rendered as child_*_{index} where index matches ordering.
        children = db.query(Child).filter_by(family_id=fam.id).order_by(Child.id.asc()).all()
        for idx, child in enumerate(children):
            name = (form.get(f"child_name_{idx}") or "").strip()
            grade = (form.get(f"child_grade_{idx}") or "").strip()
            school = (form.get(f"child_school_{idx}") or "").strip()
            child.name = name or child.name
            child.grade = grade or None
            child.school_name = school or None
            db.add(child)

        # New children come as child_*_new_{n}
        # Collect matching indexes by scanning keys
        new_indexes = set()
        for k in form.keys():
            if k.startswith("child_name_new_"):
                try:
                    new_indexes.add(int(k.split("_")[-1]))
                except ValueError:
                    pass

        for n in sorted(new_indexes):
            name = (form.get(f"child_name_new_{n}") or "").strip()
            grade = (form.get(f"child_grade_new_{n}") or "").strip()
            school = (form.get(f"child_school_new_{n}") or "").strip()
            if any([name, grade, school]):  # only create if any field filled
                db.add(Child(
                    family_id=fam.id,
                    name=name or "(Unnamed)",
                    grade=grade or None,
                    school_name=school or None,
                ))

        db.commit()

        # Preserve welcome query if present; redirect back with flash
        qs = "?welcome=1" if request.query_params.get("welcome") == "1" else ""
        return RedirectResponse(f"/app/settings{qs}&flash=Saved" if qs else "/app/settings?flash=Saved", status_code=303)

    except Exception as e:
        # Bubble a flash so you can see errors in UI
        return RedirectResponse(f"/app/settings?flash={str(e)}", status_code=303)
    finally:
        db.close()

@router.get("/settings", response_class=HTMLResponse)
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
        # views.py (inside the GET handler for settings page)
        from collections import Counter
        from sqlalchemy import select
        from .models import DigestPreference

        # collect all school_domains from all families
        rows = db.execute(select(DigestPreference.school_domains)).all()

        counter = Counter()
        for (csv_str,) in rows:
            if not csv_str:
                continue
            # normalize: split csv, trim, lowercase, strip leading '@'
            doms = {d.strip().lower().lstrip("@") for d in csv_str.split(",") if d.strip()}
            # count each unique domain once per family pref row
            for d in doms:
                counter[d] += 1

        # alphabetize
        domain_suggestions = [pair[0] for pair in sorted(counter.items(), key=lambda x: x[0])]  # [(domain, count), ...]

        # pass to template
        ctx = {
            "family": fam,
            "pref": pref,
            "user": user,
            "children": kids,
            "default_tz": os.getenv("DEFAULT_TIMEZONE","America/Los_Angeles"),
            "referral_code": rc,
            "domain_suggestions": domain_suggestions,  # <— NEW
            "welcome": request.query_params.get("welcome") == "1",
        }
        return templates.TemplateResponse("settings.html", {"request": request, **ctx})

    finally:
        db.close()

@router.post("/settings")
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
        # Save detail_level (Full vs Focused)
        dl = (form.get("detail_level") or "full").strip().lower()
        if dl not in ("full", "focused"):
            dl = "full"
        pref.detail_level = dl
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
        return RedirectResponse("/settings?flash=Saved", status_code=303)
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

def extract_original_sender_domain(forwarded_email_raw: str) -> str:
    """
    Parse the original sender domain from a forwarded email's raw content.
    This is a simple heuristic; you may want to improve it for your use case.
    """
    msg = email.message_from_string(forwarded_email_raw)
    # Try to find the first 'From:' line in the body (for forwarded content)
    body = msg.get_payload(decode=True)
    if not body:
        body = msg.get_payload()
    if isinstance(body, bytes):
        body = body.decode(errors="ignore")
    match = re.search(r"^From: .*<([^@>]+@([^>]+))>", body, re.MULTILINE)
    if match:
        domain = match.group(2).strip().lower()
        return domain
    # fallback: try to parse sender from headers
    from_addr = msg.get('From', '')
    if '@' in from_addr:
        domain = from_addr.split('@')[-1].strip().lower()
        return domain
    return None

@router.post("/inbound/forwarded-email")
async def inbound_forwarded_email(request: Request):
    db = SessionLocal()
    try:
        data = await request.body()
        raw_email = data.decode(errors="ignore")
        # Parse sender (the user who forwarded)
        msg = email.message_from_string(raw_email)
        sender = msg.get('From', '').strip().lower()
        # Find user by sender email or by to_addresses
        user = db.query(User).filter(User.email == sender).first()
        fam = None
        if user:
            fam = db.query(Family).filter_by(owner_user_id=user.id).first()
        else:
            # Try to match sender to any family's to_addresses
            prefs = db.query(DigestPreference).all()
            for pref in prefs:
                if pref.to_addresses:
                    to_list = [e.strip().lower() for e in pref.to_addresses.split(',') if e.strip()]
                    if sender in to_list:
                        fam = db.query(Family).filter_by(id=pref.family_id).first()
                        break
        if not fam:
            return {"ok": False, "reason": "Sender not recognized as user or recipient"}
        # Extract original sender domain
        orig_domain = extract_original_sender_domain(raw_email)
        if not orig_domain:
            return {"ok": False, "reason": "Could not extract original sender domain"}
        # Store in DigestPreference.school_domains (CSV)
        pref = db.query(DigestPreference).filter_by(family_id=fam.id).first()
        if not pref:
            return {"ok": False, "reason": "No DigestPreference for family"}
        domains = []
        if pref.school_domains:
            domains = [d.strip().lower() for d in pref.school_domains.split(',') if d.strip()]
        if orig_domain not in domains:
            domains.append(orig_domain)
            pref.school_domains = ','.join(sorted(set(domains)))
            db.add(pref)
            db.commit()
            added = True
        else:
            added = False
        return {"ok": True, "added": added, "domain": orig_domain}
    finally:
        db.close()