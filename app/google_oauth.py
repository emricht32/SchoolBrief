# app/google_oauth.py
import os, json
from typing import Optional, Tuple
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
]

def build_flow() -> Flow:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/auth/google/callback")

    client_config = {
        "web": {
            "client_id": client_id,
            "project_id": "schoolbrief",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": [redirect_uri],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)

def creds_from_token_json(token_json: str) -> Credentials:
    data = json.loads(token_json)
    return Credentials.from_authorized_user_info(data, scopes=SCOPES)

def token_json_from_creds(creds: Credentials) -> str:
    # Persist everything needed to rebuild/refresh next time.
    return json.dumps({
        "token": creds.token,
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if getattr(creds, "scopes", None) else SCOPES,
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
    })
