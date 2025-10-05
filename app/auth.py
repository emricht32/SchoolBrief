# app/auth.py
import os, secrets, json
from datetime import datetime, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import User, Family, DigestPreference, ProviderAccount, Subscription, ReferralCode
from .google_oauth import build_flow, token_json_from_creds
from .security import encrypt_text
from .logger import logger
import requests
from .schoology import obtain_request_token, build_authorize_url, exchange_access_token, get_or_create_schoology_provider, SchoologyAuthError, PROVIDER_NAME as SCHOOLOGY_PROVIDER
from .security import encrypt_text

router = APIRouter()

def _get_db():
    return SessionLocal()

@router.get("/google/start")
def google_start(request: Request):
    # Helpful runtime debug (shows up in logs)
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    logger.debug("OAuth start — using redirect URI: %s", redirect_uri)

    db = _get_db()
    try:
        user_email = request.session.get("user_email")
        need_consent = True
        if user_email:
            user = db.query(User).filter_by(email=user_email).first()
            if user:
                pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
                if pa and pa.token_json_enc:
                    # We already have a refresh token on file → no need to force consent again
                    need_consent = False
    finally:
        db.close()

    flow = build_flow()
    ref = request.query_params.get("ref")
    if ref:
        request.session["ref_code"] = ref

    # Only include "prompt" when we want to force consent; avoid passing None
    auth_kwargs = dict(
        access_type="offline",
        include_granted_scopes="true",
    )
    if need_consent:
        auth_kwargs["prompt"] = "consent"

    auth_url, state = flow.authorization_url(**auth_kwargs)

    logger.debug("OAuth client_id: %s", os.getenv("GOOGLE_CLIENT_ID"))
    logger.debug("OAuth redirect_uri in use (from Flow): %s", flow.redirect_uri)

    request.session["oauth_state"] = state
    return RedirectResponse(auth_url)


@router.get("/google/callback")
def google_callback(request: Request):
    state = request.session.get("oauth_state")
    if not state:
        return RedirectResponse("/?flash=Missing+state")

    flow = build_flow()
    flow.fetch_token(authorization_response=str(request.url))
    creds = flow.credentials

    # Fetch user profile
    try:
        r = requests.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {creds.token}"},
            timeout=10,
        )
        r.raise_for_status()
        info = r.json()
    except Exception:
        return RedirectResponse("/?flash=Google+userinfo+failed")

    email = info.get("email")
    name = info.get("name")

    if not email:
        return RedirectResponse("/?flash=Google+did+not+return+an+email")

    db: Session = _get_db()
    try:
        user = db.query(User).filter_by(email=email).first()
        if not user:
            user = User(email=email, name=name)
            db.add(user); db.commit(); db.refresh(user)

            fam = Family(owner_user_id=user.id, display_name=f"{name or email} Family")
            db.add(fam); db.commit(); db.refresh(fam)

            pref = DigestPreference(
                family_id=fam.id,
                cadence="weekly",
                send_time_local="07:00",
                timezone=os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
            )
            db.add(pref); db.commit()

            # 14-day trial
            sub = Subscription(
                family_id=fam.id,
                status="trialing",
                trial_end=datetime.utcnow() + timedelta(days=14),
                base_included_recipients=2,
            )
            db.add(sub); db.commit()

            # Referral code
            code = secrets.token_urlsafe(6).replace("_", "").replace("-", "")
            rc = ReferralCode(family_id=fam.id, code=code.upper())
            db.add(rc); db.commit()
        else:
            fam = db.query(Family).filter_by(owner_user_id=user.id).first()

        # Link referrer if any
        ref = request.session.get("ref_code")
        if ref and fam:
            rc = db.query(ReferralCode).filter_by(code=ref).first()
            if rc and rc.family_id != fam.id:
                sub = db.query(Subscription).filter_by(family_id=fam.id).order_by(Subscription.id.desc()).first()
                if sub:
                    sub.referrer_family_id = rc.family_id
                    db.add(sub); db.commit()

        # Store provider creds encrypted
        token_json = token_json_from_creds(creds)
        pa = db.query(ProviderAccount).filter_by(user_id=user.id, provider="google").first()
        if not pa:
            pa = ProviderAccount(user_id=user.id, provider="google", email_on_provider=email)
        pa.scopes = " ".join(creds.scopes or [])
        pa.token_json_enc = encrypt_text(token_json)
        db.add(pa); db.commit()

        # Post-login routing: send to Settings if setup is incomplete
        needs_kids = False
        if fam:
            # Try common child model names without hard dependency
            for model_name in ("Child", "Student", "Kid"):
                try:
                    models_mod = __import__(__package__ + ".models", fromlist=[model_name])
                    Model = getattr(models_mod, model_name, None)
                    if Model is not None:
                        cnt = db.query(Model).filter_by(family_id=fam.id).count()
                        needs_kids = (cnt == 0)
                        break
                except Exception:
                    # Ignore if model not present or query fails; we’ll just skip kid check
                    pass

        school_domains = (fam.prefs.school_domains if fam and fam.prefs else "") or ""
        needs_domains = (school_domains.strip() == "")

        request.session["user_email"] = email

        if needs_kids or needs_domains:
            # send them to settings with a welcome flag
            return RedirectResponse("/app/settings?welcome=1", status_code=303)

        # Otherwise, main app
        return RedirectResponse("/app", status_code=303)

    finally:
        db.close()


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/?flash=Logged+out")


# ---------------- Schoology OAuth (OAuth 1.0a) ----------------
@router.get("/schoology/start")
def schoology_start(request: Request):
    db = _get_db()
    try:
        user_email = request.session.get("user_email")
        if not user_email:
            return RedirectResponse("/?flash=Please+sign+in")
        user = db.query(User).filter_by(email=user_email).first()
        if not user:
            return RedirectResponse("/?flash=User+not+found")
        try:
            rt, rt_secret = obtain_request_token()
        except SchoologyAuthError as e:
            return RedirectResponse(f"/app/settings?flash=Schoology+auth+error%3A+{str(e)}")
        # store in session
        request.session["sch_oauth_token"] = rt
        request.session["sch_oauth_token_secret"] = rt_secret
        auth_url = build_authorize_url(rt)
        return RedirectResponse(auth_url)
    finally:
        db.close()


@router.get("/schoology/callback")
def schoology_callback(request: Request):
    rt = request.session.get("sch_oauth_token")
    rt_secret = request.session.get("sch_oauth_token_secret")
    oauth_token = request.query_params.get("oauth_token")
    oauth_verifier = request.query_params.get("oauth_verifier")
    if not (rt and rt_secret and oauth_token and oauth_verifier):
        return RedirectResponse("/app/settings?flash=Missing+Schoology+tokens")

    if oauth_token != rt:
        return RedirectResponse("/app/settings?flash=Token+mismatch")

    db = _get_db()
    try:
        user_email = request.session.get("user_email")
        if not user_email:
            return RedirectResponse("/?flash=Please+sign+in")
        user = db.query(User).filter_by(email=user_email).first()
        if not user:
            return RedirectResponse("/?flash=User+not+found")
        try:
            data = exchange_access_token(rt, rt_secret, oauth_verifier)
        except SchoologyAuthError as e:
            return RedirectResponse(f"/app/settings?flash=Schoology+exchange+failed%3A+{str(e)}")

        pa = get_or_create_schoology_provider(db, user.id)
        # store token & secret encrypted in token_json_enc
        pa.token_json_enc = encrypt_text(json.dumps(data))
        pa.scopes = ""  # Schoology doesn't use scopes like Google; keep placeholder
        db.add(pa); db.commit()
        return RedirectResponse("/app/settings?flash=Schoology+connected", status_code=303)
    finally:
        db.close()
