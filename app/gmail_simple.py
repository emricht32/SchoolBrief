# app/gmail_simple.py
import base64, io, hashlib, os, re, json
from typing import List, Dict, Optional
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from pdfminer.high_level import extract_text as pdf_extract
import html2text

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# --- WEB APP: strict (no InstalledAppFlow) ---
def gmail_service(creds: Credentials = None):
    """
    If creds are provided (from DB token), use them.
    Otherwise fall back to local token/client_secret files for CLI/local dev.
    """
    if creds:
        return build("gmail", "v1", credentials=creds)

    # ---- fallback for local dev only ----
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    token_path = os.getenv("GOOGLE_TOKEN_JSON", os.path.join(root, "token.json"))
    client_secret_path = os.getenv("GOOGLE_CLIENT_SECRET_JSON", os.path.join(root, "client_secret.json"))

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not (creds and creds.valid):
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def _parts_iter(p):
    if not p: return
    if "parts" in p:
        for c in p["parts"]:
            yield from _parts_iter(c)
    else:
        yield p

def _b64(part):
    data = (part.get("body") or {}).get("data")
    return base64.urlsafe_b64decode(data) if data else None

def _html_to_text(html: str) -> str:
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    return h.handle(html or "")

def build_query(days_back: int, allowed_domains: Optional[List[str]] = None) -> str:
    import datetime

    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=days_back)).timestamp())
    parts = [f"after:{since}"]
    if allowed_domains:
        doms = " OR ".join([f'from:@{d.lstrip("@").lower()}' for d in allowed_domains if d.strip()])
        if doms:
            parts.append(f"({doms})")
    return " ".join(parts)

def extract_text_from_message(service, msg: Dict) -> str:
    payload = msg.get("payload", {})
    plains, htmls = [], []
    for part in _parts_iter(payload):
        mime = (part.get("mimeType") or "").lower()
        if mime == "text/plain":
            b = _b64(part)
            if b: plains.append(b.decode("utf-8", errors="ignore"))
        elif mime == "text/html":
            b = _b64(part)
            if b: htmls.append(b.decode("utf-8", errors="ignore"))
        elif "application/pdf" in mime:
            body = part.get("body") or {}
            att_id = body.get("attachmentId")
            if att_id:
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg["id"], id=att_id
                ).execute()
                data = att.get("data")
                if data:
                    try:
                        pdf_bytes = base64.urlsafe_b64decode(data)
                        text = pdf_extract(io.BytesIO(pdf_bytes))
                        if text and text.strip():
                            plains.append(text)
                    except Exception:
                        pass
    txt = "\n\n".join(plains).strip()
    if not txt and htmls:
        txt = _html_to_text("\n\n".join(htmls)).strip()
    return txt


def stable_hash(subject: str, text: str) -> str:
    norm = (subject or "").strip().lower() + "\n" + re.sub(r"\s+", " ", (text or "").strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()
