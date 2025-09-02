# app/auth.py
import os, secrets, json
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import User, Family, DigestPreference, ProviderAccount, Subscription, ReferralCode
from .google_oauth import build_flow, token_json_from_creds
from .security import encrypt_text, decrypt_text
import requests

# Allow http://localhost for local dev OAuth
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

router = APIRouter()

def _get_db():
    return SessionLocal()

@router.get("/google/start")
def google_start(request: Request):
    """
    Start OAuth. Only force prompt=consent the first time to obtain a refresh_token.
    On subsequent connects, skip prompt so Google won't nag and won't drop the refresh token.
    """
    db = _get_db()
    try:
        user_email = request.session.get("user_email")
        need_consent = True
        if user_email:
            user = db.query(User).filter_by(email=user_email).first()
            if user:
                pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
                if pa and pa.token_json_enc:
                    # We already have a refresh token saved -> no need to force consent
                    need_consent = False
    finally:
        db.close()

    flow = build_flow()
    ref = request.query_params.get("ref")
    if ref:
        request.session["ref_code"] = ref

    # NOTE: prompt=None is fine; google-auth-oauthlib will omit it.
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt=("consent" if need_consent else None)
    )
    request.session["oauth_state"] = state
    return RedirectResponse(auth_url)

@router.get("/google/callback")
def google_callback(request: Request):
    """
    Finish OAuth. If Google doesn't return a refresh_token (common on subsequent auth),
    reuse the previously stored refresh_token so background refresh continues to work.
    """
    state = request.session.get("oauth_state")
    if not state:
        return RedirectResponse("/?flash=Missing+state")

    flow = build_flow()
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    # Fetch basic profile to identify the user
    try:
        r = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"}
        )
        r.raise_for_status()
        info = r.json()
    except Exception:
        return RedirectResponse("/?flash=Google+userinfo+failed")

    email = info.get("email")
    name = info.get("name") or email
    if not email:
        return RedirectResponse("/?flash=Missing+email+from+Google")

    db: Session = _get_db()
    try:
        user = db.query(User).filter_by(email=email).first()
        if not user:
            # First-time user setup
            user = User(email=email, name=name)
            db.add(user); db.commit(); db.refresh(user)

            fam = Family(owner_user_id=user.id, display_name=f"{name or email} Family")
            db.add(fam); db.commit(); db.refresh(fam)

            pref = DigestPreference(
                family_id=fam.id,
                cadence="weekly",
                send_time_local="07:00",
                timezone=os.getenv("DEFAULT_TIMEZONE","America/Los_Angeles"),
            )
            db.add(pref); db.commit()

            # Trial subscription
            sub = Subscription(
                family_id=fam.id,
                status="trialing",
                trial_end=datetime.utcnow()+timedelta(days=14),
                base_included_recipients=2,
            )
            db.add(sub); db.commit()

            # Referral code
            code = secrets.token_urlsafe(6).replace("_","").replace("-","")
            rc = ReferralCode(family_id=fam.id, code=code.upper())
            db.add(rc); db.commit()

        else:
            fam = db.query(Family).filter_by(owner_user_id=user.id).first()

        # Link referrer if any
        ref = request.session.get("ref_code")
        if ref:
            rc = db.query(ReferralCode).filter_by(code=ref).first()
            if rc and rc.family_id != fam.id:
                sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
                if sub:
                    sub.referrer_family_id = rc.family_id
                    db.add(sub); db.commit()

        # Find or create ProviderAccount for this user
        pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
        prev_refresh = None
        if pa and pa.token_json_enc:
            try:
                prev = json.loads(decrypt_text(pa.token_json_enc))
                prev_refresh = prev.get("refresh_token")
            except Exception:
                prev_refresh = None

        # If Google didn't return a refresh_token this time, reuse the old one
        if not creds.refresh_token and prev_refresh:
            creds.refresh_token = prev_refresh

        # Persist (encrypt) the token JSON (contains refresh token & expiry)
        token_json = token_json_from_creds(creds)
        if not pa:
            pa = ProviderAccount(user_id=user.id, provider="google", email_on_provider=email)
        pa.scopes = " ".join(creds.scopes or [])
        pa.token_json_enc = encrypt_text(token_json)
        db.add(pa); db.commit()

        # Sign user into app session
        request.session["user_email"] = email
        return RedirectResponse("/app")

    finally:
        db.close()

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/?flash=Logged+out")
