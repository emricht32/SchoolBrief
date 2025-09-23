# app/digest_runner.py
import os
from typing import Optional, Tuple, Dict, List

from sqlalchemy.orm import Session
from .models import DigestPreference
from .utils import csv_to_list
from .logger import logger
from .ingest_job import (
    process_forwarded_emails_and_update_domains,
    collect_recent_emails,
    process_recent_emails_saving_to_points,
)
from .compile_job import compile_and_send_digest

DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")


def _normalize_domains(csv_str: Optional[str]) -> List[str]:
    if not csv_str:
        return []
    out = []
    for d in csv_str.split(","):
        d = d.strip().lstrip("@").lower()
        if d:
            out.append(d)
    return out


def _resolve_recipients(pref: DigestPreference, user_email_fallback: Optional[str]) -> List[str]:
    to_emails = csv_to_list(pref.to_addresses)
    if not to_emails and user_email_fallback:
        to_emails = [user_email_fallback]
    return to_emails


def run_digest_once(
    db: Session,
    family_id: int,
    pref: DigestPreference,
    *,
    user_email_fallback: Optional[str] = None,
    days_back: int = 7,
) -> Tuple[bool, str, Dict[str, int]]:
    """
    Orchestrates: process forwarded → collect recent → create points → compile & send.
    Returns: (sent_ok, message, metrics)
    metrics keys: processed_forwarded, emails_fetched, processed_count, points_created
    """
    # Preconditions
    to_emails = _resolve_recipients(pref, user_email_fallback)
    if not to_emails:
        logger.debug("[DIGEST_RUNNER] run_digest_once - No recipients configured")
        return False, "No recipients configured", {
            "processed_forwarded": 0,
            "emails_fetched": 0,
            "processed_count": 0,
            "points_created": 0,
        }

    allowed_domains = _normalize_domains(pref.school_domains)
    cadence = (pref.cadence or "weekly").strip().lower()
    tz_name = pref.timezone or DEFAULT_TZ

    # Step A: forwarded emails domain maintenance
    processed_forwarded = process_forwarded_emails_and_update_domains(db)
    logger.info(f"[family_id={family_id}] processed_forwarded={processed_forwarded}")

    # Step B: collect and process recent emails
    emails = collect_recent_emails(
        db=db,
        family_id=family_id,
        allowed_domains=allowed_domains,
        days_back=days_back,
    )
    processed_count, points_created = process_recent_emails_saving_to_points(
        db=db,
        family_id=family_id,
        emails=emails,
        local_tz=tz_name,
    )
    logger.info(
        f"[family_id={family_id}] emails_fetched={len(emails)} "
        f"processed_count={processed_count} points_created={points_created}"
    )

    # Step C: compile + send
    sent, msg = compile_and_send_digest(
        db=db,
        family_id=family_id,
        to_emails=to_emails,
        cadence=cadence,
    )
    return bool(sent), (msg or "sent" if sent else "not sent"), {
        "processed_forwarded": int(processed_forwarded or 0),
        "emails_fetched": len(emails),
        "processed_count": int(processed_count or 0),
        "points_created": int(points_created or 0),
    }
