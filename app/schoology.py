import os, time, json, hmac, hashlib, base64, random, urllib.parse
from typing import Optional, Dict, Any, List, Tuple
import requests
from requests_oauthlib import OAuth1
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from .models import ProviderAccount, SchoologyItem, Family
from .logger import logger

SCHO_BASE = os.getenv("SCHOOLOGY_API_BASE", "https://api.schoology.com/v1")

# Environment variables required:
#   SCHOOLGY_CONSUMER_KEY
#   SCHOOLGY_CONSUMER_SECRET
#   SCHOOLGY_CALLBACK_URL (matches app route)
# We'll store the user access token & secret in ProviderAccount as scopes=... and token_json_enc (JSON) reusing encryption if desired
# For simplicity we will not encrypt yet; reuse existing encryption helpers if needed.

from .security import encrypt_text, decrypt_text

PROVIDER_NAME = "schoology"

class SchoologyAuthError(Exception):
    pass

def _get_consumer() -> Tuple[str,str]:
    ck = os.getenv("SCHOOLGY_CONSUMER_KEY")
    cs = os.getenv("SCHOOLGY_CONSUMER_SECRET")
    if not ck or not cs:
        raise SchoologyAuthError("Missing Schoology consumer key/secret env vars")
    return ck, cs


def _schoology_oauth_session(token: Optional[str], token_secret: Optional[str]):
    ck, cs = _get_consumer()
    return OAuth1(ck, cs, token, token_secret, signature_method='HMAC-SHA1')


def _request(db: Session, pa: ProviderAccount, method: str, path: str, params: Dict[str,Any]=None) -> Dict[str,Any]:
    url = f"{SCHO_BASE}{path}" if not path.startswith("http") else path
    token_data = {}
    if pa.token_json_enc:
        try:
            token_data = json.loads(decrypt_text(pa.token_json_enc))
        except Exception:
            pass
    auth = _schoology_oauth_session(token_data.get("oauth_token"), token_data.get("oauth_token_secret"))
    r = requests.request(method.upper(), url, params=params, auth=auth, timeout=20)
    if r.status_code >= 400:
        raise SchoologyAuthError(f"Schoology API error {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


def get_or_create_schoology_provider(db: Session, user_id: int) -> ProviderAccount:
    pa = db.query(ProviderAccount).filter_by(user_id=user_id, provider=PROVIDER_NAME).first()
    if not pa:
        pa = ProviderAccount(user_id=user_id, provider=PROVIDER_NAME)
        db.add(pa); db.commit(); db.refresh(pa)
    return pa

# ---- OAuth 1 flow helpers ----

def obtain_request_token() -> Tuple[str,str]:
    ck, cs = _get_consumer()
    callback = os.getenv("SCHOOLGY_CALLBACK_URL")
    if not callback:
        raise SchoologyAuthError("Missing SCHOOLGY_CALLBACK_URL")
    oauth = OAuth1(ck, cs, callback_uri=callback)
    r = requests.post(f"{SCHO_BASE}/oauth/request_token", auth=oauth, timeout=20)
    if r.status_code != 200:
        raise SchoologyAuthError(f"Failed to get request token: {r.text}")
    data = dict(urllib.parse.parse_qsl(r.text))
    return data['oauth_token'], data['oauth_token_secret']


def build_authorize_url(oauth_token: str) -> str:
    return f"{SCHO_BASE}/oauth/authorize?oauth_token={urllib.parse.quote(oauth_token)}"


def exchange_access_token(oauth_token: str, oauth_token_secret: str, oauth_verifier: str) -> Dict[str,str]:
    ck, cs = _get_consumer()
    oauth = OAuth1(ck, cs, oauth_token, oauth_token_secret, verifier=oauth_verifier)
    r = requests.post(f"{SCHO_BASE}/oauth/access_token", auth=oauth, timeout=20)
    if r.status_code != 200:
        raise SchoologyAuthError(f"Failed to exchange access token: {r.text}")
    return dict(urllib.parse.parse_qsl(r.text))

# ---- Fetchers ----

def fetch_me(db: Session, pa: ProviderAccount) -> Dict[str,Any]:
    return _request(db, pa, "GET", "/users/me")


def fetch_sections(db: Session, pa: ProviderAccount) -> List[Dict[str,Any]]:
    data = _request(db, pa, "GET", "/users/me/sections")
    return data.get('section', []) if isinstance(data, dict) else []


def fetch_section_assignments(db: Session, pa: ProviderAccount, section_id: str) -> List[Dict[str,Any]]:
    # assignments endpoint
    data = _request(db, pa, "GET", f"/sections/{section_id}/assignments")
    return data.get('assignment', []) if isinstance(data, dict) else []


def fetch_section_events(db: Session, pa: ProviderAccount, section_id: str) -> List[Dict[str,Any]]:
    data = _request(db, pa, "GET", f"/sections/{section_id}/events")
    return data.get('event', []) if isinstance(data, dict) else []

# ---- Persistence / normalization ----

def _parse_dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    # Schoology returns Unix timestamp (seconds) in some fields or ISO? We'll attempt both.
    try:
        if ts.isdigit():
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        # fallback parse
        return datetime.fromisoformat(ts.replace('Z','+00:00'))
    except Exception:
        return None


def store_items(db: Session, family_id: int, pa: ProviderAccount, items: List[Dict[str,Any]], item_type: str, course_title: Optional[str]):
    created = 0
    for it in items:
        sid = str(it.get('id') or it.get('assignment_id') or it.get('event_id') or '')
        if not sid:
            continue
        existing = db.query(SchoologyItem).filter_by(family_id=family_id, schoology_id=sid).first()
        due_ts = _parse_dt(str(it.get('due')) if it.get('due') else str(it.get('start')))
        title = it.get('title') or it.get('name') or '(Untitled)'
        desc = it.get('description') or ''
        if existing:
            # update minimal fields
            existing.title = title[:500]
            existing.description = (desc or '')[:4000]
            existing.due_at = due_ts
            existing.updated_at = datetime.utcnow()
            existing.course_title = course_title
            db.add(existing)
            continue
        db.add(SchoologyItem(
            family_id=family_id,
            provider_account_id=pa.id,
            schoology_id=sid,
            item_type=item_type,
            title=title[:500],
            description=(desc or '')[:4000],
            due_at=due_ts,
            course_title=course_title,
            raw_json=json.dumps(it)[:10000],
        ))
        created += 1
    if created:
        db.commit()
    return created


def sync_schoology(db: Session, family_id: int) -> Dict[str,int]:
    fam = db.query(Family).filter_by(id=family_id).first()
    if not fam:
        return {"created":0, "updated":0}
    pa = db.query(ProviderAccount).filter_by(user_id=fam.owner_user_id, provider=PROVIDER_NAME).first()
    if not pa or not pa.token_json_enc:
        return {"created":0, "updated":0}

    sections = fetch_sections(db, pa)
    created_total = 0
    for s in sections:
        sec_id = str(s.get('id') or '')
        course_title = s.get('course_title') or s.get('course_title_display') or None
        if not sec_id:
            continue
        try:
            assigns = fetch_section_assignments(db, pa, sec_id)
            created_total += store_items(db, family_id, pa, assigns, 'assignment', course_title)
        except Exception as e:
            logger.debug(f"Schoology assignments fetch failed section={sec_id}: {e}")
        try:
            events = fetch_section_events(db, pa, sec_id)
            created_total += store_items(db, family_id, pa, events, 'event', course_title)
        except Exception as e:
            logger.debug(f"Schoology events fetch failed section={sec_id}: {e}")

    return {"created": created_total, "updated": 0}


def materialize_schoology_items_as_oneliners(db: Session, family_id: int) -> int:
    """Create OneLiner rows from SchoologyItem if not already summarized.

    We use schoology_id as source_msg_id with a prefix to avoid clash with gmail ids.
    """
    from .models import OneLiner, SchoologyItem  # local import to avoid circular
    created = 0
    rows = db.query(SchoologyItem).filter_by(family_id=family_id).all()
    for r in rows:
        source_id = f"sch_{r.schoology_id}"[:128]
        exists = db.query(OneLiner).filter_by(family_id=family_id, source_msg_id=source_id).first()
        if exists:
            continue
        # Build one-liner text
        when_dt = r.due_at
        date_string = when_dt.strftime('%Y-%m-%d') if when_dt else None
        time_string = when_dt.strftime('%I:%M %p').lstrip('0') if when_dt else None
        course_part = f"[{r.course_title}] " if r.course_title else ""
        one = f"{course_part}{r.title}"[:200]
        db.add(OneLiner(
            family_id=family_id,
            source_msg_id=source_id,
            one_liner=one,
            when_ts=when_dt,
            date_string=date_string,
            time_string=time_string,
            domain='schoology.com'
        ))
        created += 1
    if created:
        db.commit()
    return created
