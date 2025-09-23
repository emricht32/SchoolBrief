# app/google_oauth.py
import os, json
from typing import Tuple
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
]

def _effective_redirect_uri() -> str:
    public_base = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI", f"{public_base}/auth/google/callback")

def _configure_transport_security(redirect_uri: str) -> None:
    """
    Only allow insecure transport (http) in local dev. Never enable it in prod.
    """
    if redirect_uri.startswith("http://localhost") or redirect_uri.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    else:
        # Make sure it’s OFF in production
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

def build_flow() -> Flow:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = _effective_redirect_uri()
    _configure_transport_security(redirect_uri)

    client_config = {
        "web": {
            "client_id": client_id,
            "project_id": os.getenv("GOOGLE_PROJECT_ID", "schoolbrief"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    return flow

def creds_from_token_json(token_json: str) -> Credentials:
    data = json.loads(token_json)
    creds = Credentials.from_authorized_user_info(data, scopes=SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def token_json_from_creds(creds: Credentials) -> str:
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
    }
    return json.dumps(data)
