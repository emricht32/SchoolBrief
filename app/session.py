# app/session.py
import os
from urllib.parse import urlparse
from starlette.middleware.sessions import SessionMiddleware

def add_session_middleware(app):
    """
    Adds SessionMiddleware with secure defaults in production (https),
    and convenient/dev-friendly defaults on localhost.
    """
    secret = (
        os.getenv("SESSION_SECRET")
        or os.getenv("SESSION_SECRET_KEY")
        or os.getenv("APP_SECRET_KEY")
        or "dev-insecure-secret"  # dev fallback only
    )

    cookie_name = os.getenv("SESSION_COOKIE_NAME", "sb_session")
    public_base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    p = urlparse(public_base)

    is_local = p.hostname in ("localhost", "127.0.0.1")
    is_https = (p.scheme == "https")

    # Only set cookie domain when not localhost
    cookie_domain = None if is_local else p.hostname

    # Force Secure cookies when https; allow override via env
    https_only = bool(int(os.getenv("SESSION_HTTPS_ONLY", "1" if is_https else "0")))
    same_site = os.getenv("SESSION_SAMESITE", "lax")  # good default for web apps
    max_age = int(os.getenv("SESSION_MAX_AGE", str(30 * 24 * 60 * 60)))  # 30 days

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie=cookie_name,
        https_only=https_only,
        same_site=same_site,
        max_age=max_age,
        domain=cookie_domain,
        path="/",
    )
