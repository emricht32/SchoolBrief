# app/ingest_job.py
from datetime import datetime, timezone
import email as email_mod
from email import message_from_string
from email.message import Message
from email.utils import parseaddr
from googleapiclient.errors import HttpError
import imaplib
from sqlalchemy.orm import Session
from typing import List, Tuple, Optional, Dict
import os, pytz, re
from .gmail_simple import (
    build_query,
    extract_text_from_message,
    stable_hash,
)
from .gmail_tokens import gmail_service_for_family, GoogleAuthError
from .emailer import send_reconnect_email
from .security import encrypt_text
from .llm import summarize_email_to_points
from .logger import logger
from .models import OneLiner, ProcessedEmail, ProviderAccount, Family, DigestPreference, User

EMAIL_RE = re.compile(
    r'(?mi)^\s*From:\s*(?:.+?)?<\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s*>'
    r'|^\s*From:\s*([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s*$',
    re.I
)

def _extract_email_from_header(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    _, addr = parseaddr(value)
    return addr or None

def _first_email_in_text(text: str) -> Optional[str]:
    # Look for a "From:" line in forwarded blocks
    for m in EMAIL_RE.finditer(text):
        return m.group(1) or m.group(2)
    return None

def _find_original_from_in_parts(msg: Message) -> Optional[str]:
    """
    Prefer a real attached message/rfc822 (proper forward).
    Fallback to scanning text parts for a forwarded 'From:' line.
    """
    # 1) Proper forwards: attached message/rfc822
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            # payload may be a list of Message objects or a raw Message
            if isinstance(payload, list) and payload:
                inner = payload[0]
            else:
                inner = payload if isinstance(payload, Message) else None
            if isinstance(inner, Message):
                addr = _extract_email_from_header(inner.get("From"))
                if addr:
                    return addr

    # 2) Inline forwards: scan text/plain then text/html
    text_candidates = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html"):
            try:
                text_candidates.append(part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace"))
            except Exception:
                continue

    # Search text parts (plain first, then html)
    for text in text_candidates:
        addr = _first_email_in_text(text)
        if addr:
            return addr

    return None

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

    try:
        service = gmail_service_for_family(db, family_id)
    except GoogleAuthError as e:
        # Attempt recovery: clear the broken token, notify user, and hint they must reconnect.
        # NOTE: This background job cannot "log the user out" (no request/session context).
        # Web requests should check for missing provider token and force a reconnect UX.
        fam_owner = db.query(Family).filter_by(id=family_id).first()
        if fam_owner:
            user = db.query(User).filter_by(id=fam_owner.owner_user_id).first()
            if user:
                pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
                if pa and pa.token_json_enc:
                    pa.token_json_enc = None
                    db.add(pa); db.commit()
                # Fire-and-forget email (best effort)
                try:
                    send_reconnect_email(user)
                except Exception:
                    logger.debug("send_reconnect_email failed during ingest", exc_info=True)
        raise RuntimeError(str(e))


    q = build_query(days_back=days_back, allowed_domains=allowed_domains)
    logger.debug(f"[INGEST] family_id={family_id} days_back={days_back} domains={allowed_domains}")
    logger.debug(f"[INGEST] Gmail query (with domains): {q}")

    try:
        ids = _list_all_ids(service, q)
    except HttpError as he:
        logger.debug(f"[INGEST] List error: {he.status_code if hasattr(he,'status_code') else ''} {he}")
        return 0, 0

    logger.debug(f"[INGEST] Found {len(ids)} message(s) with domain filter)")

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
            logger.debug(f"[INGEST] HttpError on message {mid}: status={status} body={err_body[:200]}")
            continue
        except Exception as e:
            logger.debug(f"[INGEST] Connection/Other error on message {mid}: {type(e).__name__}: {e}")
            continue

    return emails

def process_recent_emails_saving_to_points(
        db: Session,
        family_id: int,
        emails: Dict, 
        local_tz: str = "America/Los_Angeles",
) -> Tuple[int, int]:
    processed_count = 0
    points_created = 0
    created_local = 0
    try:
        service = gmail_service_for_family(db, family_id)
    except GoogleAuthError as e:
        # Same recovery logic here: clear token & notify user. See note above re: logout limitations.
        fam_owner = db.query(Family).filter_by(id=family_id).first()
        if fam_owner:
            user = db.query(User).filter_by(id=fam_owner.owner_user_id).first()
            if user:
                pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
                if pa and pa.token_json_enc:
                    pa.token_json_enc = None
                    db.add(pa); db.commit()
                try:
                    send_reconnect_email(user)
                except Exception:
                    logger.debug("send_reconnect_email failed during process_recent_emails", exc_info=True)
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
            logger.debug(f"[INGEST] extract_text failed ({subj}): {e}")
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
            logger.debug(f"[INGEST] {len(points)} points from LLM")

            for i, p in enumerate(points):
                logger.debug(f"    [{i}] one_liner={p.get('one_liner')!r} date_string={p.get('date_string')!r} time_string={p.get('time_string')!r}")

        except Exception as e:
            logger.debug(f"[INGEST] LLM error for ({subj}): {e}")
            continue

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

    logger.debug(f"[INGEST] Done: processed={processed_count}, new_points={points_created}")
    return processed_count, points_created

def extract_senders(raw_email: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (current_sender_email, original_forwarded_sender_email)

    - current_sender_email: from the top-level 'From' header.
    - original_forwarded_sender_email: from an attached message/rfc822 or an inline
      forwarded block's 'From:' line.
    """
    msg = message_from_string(raw_email)

    current_from = _extract_email_from_header(msg.get("From"))
    original_from = _find_original_from_in_parts(msg)

    # Extra fallback: if nothing found in parts, scan the raw body after headers.
    if not original_from:
        # Split headers/body and scan the body only to avoid matching the top header's From:
        split = raw_email.split("\n\n", 1)
        if len(split) == 2:
            original_from = _first_email_in_text(split[1])

    return current_from, original_from

def process_forwarded_emails_and_update_domains(db: Session):
    """
    Connect to addschoolbrief@gmail.com via IMAP and app password (from env vars),
    fetch unprocessed emails, extract the ORIGINAL sender's domain from forwarded emails,
    and update DigestPreference.school_domains.

    Notes:
    - Uses extract_senders(raw_email_str) to get (current_from, original_from).
    - Falls back to parsing top-level From only if an original forwarded sender isn't found.
    """
    IMAP_HOST = os.getenv("FORWARD_IMAP_HOST", "imap.gmail.com")
    IMAP_USER = os.getenv("FORWARD_IMAP_USER", "addschoolbrief@gmail.com")
    IMAP_PASS = os.getenv("FORWARD_IMAP_PASS")
    IS_PROD = os.getenv("APP_ENV", os.getenv("ENV", "")).lower() in ("prod", "production")

    def mark_seen(msg_num: bytes):
        if IS_PROD:
            try:
                mail.store(msg_num, '+FLAGS', '\\Seen')
            except Exception as e:
                logger.exception(f"[FORWARD-INGEST] Failed to mark seen: {e}")
        else:
            logger.debug("[FORWARD-INGEST] Non-prod run; leaving message UNSEEN")

    if not IMAP_PASS:
        logger.warning("IMAP_PASS is NULL")
        return 0

    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASS)
    try:
        mail.select("inbox")
        # Search for all unseen emails
        typ, data = mail.search(None, 'UNSEEN')
        logger.debug(f"[FORWARD-INGEST] IMAP search type={typ} data={data}")
        if typ != 'OK':
            return 0

        msg_nums = data[0].split() if data and data[0] else []
        new_domains = 0

        for num in msg_nums:
            typ, msg_data = mail.fetch(num, '(RFC822)')
            logger.debug(f"[FORWARD-INGEST] IMAP fetch type={typ} msg_data_type={type(msg_data)}")
            if typ != 'OK' or not msg_data or not msg_data[0]:
                continue

            raw_bytes = msg_data[0][1]
            eml = email_mod.message_from_bytes(raw_bytes)
            raw_str = None
            try:
                raw_str = raw_bytes.decode('utf-8', errors='replace')
            except Exception:
                # Extremely rare; leave as None to skip extract_senders raw path fallback
                pass

            # --- Use the helper to extract both senders ---
            current_from, original_from = extract_senders(raw_str or eml.as_string())

            # Normalize current sender (used to find the Family)
            current_sender = (current_from or _extract_email_from_header(eml.get("From")) or "").strip().lower()
            logger.debug(f"[FORWARD-INGEST] current_sender={current_sender} original_from={original_from}")

            # Try to match sender to a user or to_addresses
            fam = None
            if current_sender:
                user = db.query(User).filter(User.email == current_sender).first()
            else:
                user = None

            if user:
                logger.debug(f"[FORWARD-INGEST] user found with email={user.email}")
                fam = db.query(Family).filter_by(owner_user_id=user.id).first()
            else:
                logger.debug(f"[FORWARD-INGEST] user not found or no current_sender; scanning to_addresses")
                prefs = db.query(DigestPreference).all()
                for pref in prefs:
                    if pref.to_addresses:
                        to_list = [e.strip().lower() for e in pref.to_addresses.split(',') if e.strip()]
                        if current_sender and current_sender in to_list:
                            fam = db.query(Family).filter_by(id=pref.family_id).first()
                            break
                        # If current sender is empty (rare), still allow matching by the list containing IMAP_USER
                        if not current_sender and IMAP_USER.lower() in to_list:
                            fam = db.query(Family).filter_by(id=pref.family_id).first()
                            break

            if not fam:
                logger.debug("[FORWARD-INGEST] No family matched; skipping message")
                # Still mark as seen to avoid infinite reprocessing
                mark_seen(num)
                continue

            # --- Derive domain from the ORIGINAL forwarded sender ---
            orig_email = (original_from or "").strip().lower()
            if not orig_email:
                logger.debug("[FORWARD-INGEST] No original forwarded sender found; skipping domain update")
                mail.store(num, '+FLAGS', '\\Seen')
                continue

            if '@' not in orig_email:
                logger.debug(f"[FORWARD-INGEST] original_from lacks '@': {orig_email!r}; skipping")
                mark_seen(num)
                continue

            orig_domain = orig_email.split('@', 1)[-1]
            if not orig_domain:
                logger.debug("[FORWARD-INGEST] Empty original domain; skipping")
                mark_seen(num)
                continue

            # Store in DigestPreference.school_domains (CSV)
            pref = db.query(DigestPreference).filter_by(family_id=fam.id).first()
            if not pref:
                logger.debug(f"[FORWARD-INGEST] No DigestPreference for family_id={fam.id}")
                mark_seen(num)
                continue

            domains = []
            if pref.school_domains:
                domains = [d.strip().lower() for d in pref.school_domains.split(',') if d.strip()]

            if orig_domain not in domains:
                domains.append(orig_domain)
                pref.school_domains = ','.join(sorted(set(domains)))
                db.add(pref)
                db.commit()
                new_domains += 1
                logger.info(f"[FORWARD-INGEST] Added domain '{orig_domain}' for family_id={fam.id}")

            # Mark as processed in your DB
            db.add(ProcessedEmail(
                family_id=fam.id,
                gmail_msg_id=str(num),  # NOTE: this is a sequence number, not a stable Gmail ID
                content_hash="domain_add",
                subject=eml.get('Subject', '')[:1000],
                processed_at=datetime.now(timezone.utc),
            ))
            db.commit()

            # Mark email as seen
            mark_seen(num)

        return new_domains
    finally:
        mail.logout()
