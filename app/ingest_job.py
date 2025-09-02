# app/ingest_job.py
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional, Dict
from sqlalchemy.orm import Session
import pytz, re
from .models import OneLiner, ProcessedEmail, ProviderAccount, Family
from .gmail_simple import (
    build_query,
    extract_text_from_message,
    stable_hash,
)
from .llm import summarize_email_to_points
from googleapiclient.errors import HttpError  # NEW
from .gmail_tokens import gmail_service_for_family, GoogleAuthError
from .logger import logger
from email.utils import parseaddr


def _email_headers(msg) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return {
        "from": headers.get("from", "") or "",
        "subject": headers.get("subject", "") or "",
        "date": headers.get("date", "") or "",
        "message_id": headers.get("message-id", "") or "",
    }

def _list_all_ids(service, q: str, page_size: int = 100) -> list[str]:
    ids = []
    page_token = None
    while True:
        resp = service.users().messages().list(
            userId="me", q=q, maxResults=page_size, pageToken=page_token
        ).execute(num_retries=3)  # RETRIES on list
        ids.extend([m["id"] for m in resp.get("messages", [])])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids


DEFAULT_ALLDAY_LOCAL_HOUR = 8  # 8:00 AM local for date-only items

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ZERO_CLOCK_RE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}[T ]|)(00:00(?::00(?:\.0+)?)?)(?:Z|[+\-]\d{2}:\d{2})?$")

def _to_when_ts_and_flag(when_iso: str, local_tz: str) -> Tuple[Optional[datetime], bool]:
    if not when_iso:
        return (None, False)
    s = when_iso.strip()

    try:
        tz = pytz.timezone(local_tz)

        if _DATE_ONLY_RE.match(s):
            y, m, d = map(int, s.split("-"))
            # choose is_dst for DST gaps/ambiguity (False = standard time)
            local = tz.localize(datetime(y, m, d, DEFAULT_ALLDAY_LOCAL_HOUR, 0, 0), is_dst=False)
            return (local.astimezone(timezone.utc), False)

        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # naive → interpret as local
            try:
                dt = tz.localize(dt, is_dst=None)  # raise on ambiguous/non-existent
            except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError):
                # pick a policy; here we bias to standard time fallback
                dt = tz.localize(dt, is_dst=False)

        if _ZERO_CLOCK_RE.match(s):
            dt_local = dt.astimezone(tz)
            pinned = dt_local.replace(hour=DEFAULT_ALLDAY_LOCAL_HOUR, minute=0, second=0, microsecond=0)
            # Handle nonexistent time after replacing hour:
            try:
                # re-localize not needed (aware), but ensure it’s a valid local time
                pinned_utc = pinned.astimezone(timezone.utc)
            except (pytz.NonExistentTimeError, pytz.AmbiguousTimeError):
                # nudge forward 1 hour in gaps
                pinned = pinned.replace(hour=DEFAULT_ALLDAY_LOCAL_HOUR + 1)
                pinned_utc = pinned.astimezone(timezone.utc)
            return (pinned_utc, False)

        return (dt.astimezone(timezone.utc), True)

    except Exception:
        return (None, False)

def collect_recent_emails(
    db: Session,
    family_id: int,
    allowed_domains: List[str],
    days_back: int = 7,
 ) -> list:
    fam = db.query(Family).filter_by(id=family_id).first()
    if not fam:
        raise RuntimeError("Family not found.")
    prov = db.query(ProviderAccount).filter_by(user_id=fam.owner_user_id, provider="google").first()
    if not prov or not prov.token_json_enc:
        raise RuntimeError("No Google account connected. Connect Google in Settings.")
    if not allowed_domains:
        raise RuntimeError(
            "No school/activity domains configured. "
            "Please go to Settings → Family and enter at least one domain "
            "(e.g. parentsquare.com, schoology.com)."
        )

    # REPLACE with:
    try:
        service = gmail_service_for_family(db, family_id)
    except GoogleAuthError as e:
        raise RuntimeError(str(e))


    q = build_query(days_back=days_back, allowed_domains=allowed_domains)
    print(f"[INGEST] family_id={family_id} days_back={days_back} domains={allowed_domains}")
    print(f"[INGEST] Gmail query (with domains): {q}")

    try:
        ids = _list_all_ids(service, q)
    except HttpError as he:
        print(f"[INGEST] List error: {he.status_code if hasattr(he,'status_code') else ''} {he}")
        return 0, 0

    print(f"[INGEST] Found {len(ids)} message(s) with domain filter)")

    emails = []
    for mid in ids:
        try:
            # RETRIES on get()
            msg = service.users().messages().get(userId="me", id=mid, format="full").execute(num_retries=3)
            emails.append(msg)
        except HttpError as he:
            # LOG REAL ERROR DETAILS
            status = getattr(he, "status_code", None)
            err_body = getattr(he, "content", b"").decode("utf-8", errors="ignore")
            print(f"[INGEST] HttpError on message {mid}: status={status} body={err_body[:200]}")
            continue
        except Exception as e:
            print(f"[INGEST] Connection/Other error on message {mid}: {type(e).__name__}: {e}")
            continue

    return emails

def process_recent_emails_saving_to_points(
        db: Session,
        family_id: int,
        emails: Dict, 
        local_tz: str = "America/Los_Angeles",
) -> Tuple[int, int]:
    try:
        service = gmail_service_for_family(db, family_id)
    except GoogleAuthError as e:
        raise RuntimeError(str(e))
    
    for msg in emails:
        hdr = _email_headers(msg)
        subj = hdr.get("subject", "")
        sender = hdr.get("from", "")
        # Safely parse email address from "From" header
        _, addr = parseaddr(sender)  # e.g., "Mrs. Smith <teacher@schoology.com>"
        domain = None
        if addr and "@" in addr:
            domain = addr.split("@")[-1].lower()

        try:
            body_text = extract_text_from_message(service, msg)
        except Exception as e:
            print(f"[INGEST] extract_text failed ({subj}): {e}")
            continue

        if not (body_text and body_text.strip()):
            # Skip empty bodies quietly
            continue

        h = stable_hash(subj, body_text)
        if db.query(ProcessedEmail).filter_by(family_id=family_id, content_hash=h).first():
            # Already processed
            continue

        # Summarize with LLM
        try:
            points = summarize_email_to_points(subj, body_text, local_tz=local_tz, domain=domain) or []
            print(f"[INGEST] {len(points)} points from LLM")

            for i, p in enumerate(points):
                print(f"    [{i}] one_liner={p.get('one_liner')!r} when_iso={p.get('when_iso')!r}")

        except Exception as e:
            print(f"[INGEST] LLM error for ({subj}): {e}")
            continue
        processed_count = 0
        points_created = 0
        created_local = 0
        # inside process_recent_emails(...)

        for p in points:
            one = (p.get("one_liner") or "").strip()
            if not one:
                continue

            when_ts = None
            time_was_explicit = False
            when_iso = (p.get("when_iso") or "").strip()
            date_string = (p.get("date_string") or "").strip()
            time_string = (p.get("time_string") or "").strip()

            if when_iso:
                when_ts, time_was_explicit = _to_when_ts_and_flag(when_iso, local_tz)

            # logger.debug("[ADD OneLiner] %s | %s | %s", one, when_ts, when_iso)
            db.add(OneLiner(
                family_id=family_id,
                source_msg_id=(hdr.get("message_id") or "Unknown")[:128],
                one_liner=one[:200],
                when_ts=when_ts,
                created_at=datetime.now(timezone.utc),
                date_string=date_string,         # NEVER None
                time_string=time_string,         # NEVER None
                domain=domain,                   # NEVER None
            ))
            created_local += 1

        # NEW — ProcessedEmail (columns: gmail_msg_id, subject, processed_at)       
        db.add(ProcessedEmail(
            family_id=family_id,
            gmail_msg_id=(hdr.get("message_id") or "unknown")[:128],
            content_hash=h,
            subject=(subj or "")[:1000],
            processed_at=datetime.now(timezone.utc),
        ))
        db.commit()

        processed_count += 1
        points_created += created_local

    print(f"[INGEST] Done: processed={processed_count}, new_points={points_created}")
    return processed_count, points_created