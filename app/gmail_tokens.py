# app/gmail_tokens.py
from __future__ import annotations
import json
from typing import Optional, Tuple

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from .models import ProviderAccount, Family
from .security import encrypt_text, decrypt_text
from .google_oauth import creds_from_token_json, token_json_from_creds


class GoogleAuthError(Exception):
    """Raised when Google credentials cannot be loaded/refreshed."""


def _load_provider_account_for_user(db: Session, user_id: int) -> Optional[ProviderAccount]:
    return db.query(ProviderAccount).filter_by(user_id=user_id, provider="google").first()


def _load_provider_account_for_family_owner(db: Session, family_id: int) -> Optional[ProviderAccount]:
    fam = db.query(Family).filter_by(id=family_id).first()
    if not fam:
        return None
    return _load_provider_account_for_user(db, fam.owner_user_id)


def _rehydrate_creds(pa: ProviderAccount) -> Credentials:
    if not pa or not pa.token_json_enc:
        raise GoogleAuthError("No Google account connected for this user.")

    try:
        token_json = decrypt_text(pa.token_json_enc)
        creds = creds_from_token_json(token_json)  # handles immediate refresh if expired & refresh_token exists
        return creds
    except Exception as e:
        raise GoogleAuthError(f"Failed to rehydrate Google credentials: {e}")


def _maybe_refresh_and_persist(db: Session, pa: ProviderAccount, creds: Credentials) -> Credentials:
    """
    If creds are expired and we have a refresh_token, refresh them and persist the new token JSON.
    Always persist if the access token/expiry changed (so the DB stays current).
    """
    before_token = getattr(creds, "token", None)
    before_expiry = getattr(creds, "expiry", None)

    # Attempt refresh when necessary
    if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
        try:
            creds.refresh(Request())
        except Exception as e:
            # Common when refresh token was revoked or invalid
            raise GoogleAuthError(f"Google token refresh failed: {e}")

    # Persist updates if token or expiry changed
    after_token = getattr(creds, "token", None)
    after_expiry = getattr(creds, "expiry", None)
    if (after_token != before_token) or (after_expiry != before_expiry):
        try:
            pa.token_json_enc = encrypt_text(token_json_from_creds(creds))
            db.add(pa)
            db.commit()
        except Exception:
            db.rollback()
            # Not fatal to the request, but worth surfacing for logs
            raise GoogleAuthError("Failed to persist refreshed Google credentials.")

    return creds

def get_google_creds_for_family(db: Session, family_id: int) -> Credentials:
    """
    Same as above, but looks up the family's owner user.
    """
    pa = _load_provider_account_for_family_owner(db, family_id)
    if not pa:
        raise GoogleAuthError("No Google provider account found for family owner.")
    creds = _rehydrate_creds(pa)
    return _maybe_refresh_and_persist(db, pa, creds)

def gmail_service_for_family(db: Session, family_id: int):
    """
    Convenience: return a ready Gmail API service for the family owner.
    """
    creds = get_google_creds_for_family(db, family_id)
    return build("gmail", "v1", credentials=creds)
