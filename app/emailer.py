# app/emailer.py
import os, smtplib, ssl
from email.message import EmailMessage
from .logger import logger

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")  # must be full email for Gmail
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USERNAME)  # default to username
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "SchoolBrief")

def _from_addr() -> str:
    logger.debug("")
    name = (SMTP_FROM_NAME or "").strip()
    email = (SMTP_FROM_EMAIL or "").strip()
    if not email:
        raise RuntimeError("SMTP_FROM_EMAIL/SMTP_USERNAME not configured")
    if name:
        return f"{name} <{email}>"
    return email

def send_email(subject: str, html: str, text: str, to_addrs: list[str]):
    logger.debug("")
    if not (SMTP_USERNAME and SMTP_PASSWORD):
        raise RuntimeError("SMTP credentials are not configured")

    # Build message
    msg = EmailMessage()
    msg["Subject"] = subject or "SchoolBrief"
    msg["From"] = _from_addr()
    msg["To"] = ", ".join(to_addrs or [])
    # text first, then add HTML alternative
    msg.set_content(text or "")
    if html:
        msg.add_alternative(html, subtype="html")

    try:
        # STARTTLS (port 587)
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

    except smtplib.SMTPAuthenticationError as e:
        # Provide human-friendly hints
        hint = (
            "Gmail rejected the credentials. Make sure you are using a 16-character App Password, "
            "the username is the full email, and SMTP_FROM_EMAIL matches the Gmail account. "
            "If you changed your Google password, "
            "you must create a new Gmail App Password and update SMTP_PASSWORD"
            "Update your .env and restart the app."
        )
        # e.smtp_error is bytes; decode safely
        detail = ""
        try:
            detail = (e.smtp_error or b"").decode("utf-8", errors="ignore")
        except Exception:
            pass
        raise RuntimeError(f"SMTP auth failed (code {e.smtp_code}): {detail} — {hint}") from e

    except smtplib.SMTPException as e:
        raise RuntimeError(f"SMTP error: {type(e).__name__}: {e}") from e

    except Exception as e:
        raise RuntimeError(f"Email send failed: {type(e).__name__}: {e}") from e
