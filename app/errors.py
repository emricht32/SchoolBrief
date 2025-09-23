# app/errors.py
import re, socket, uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Type

# Optional imports (type-only)
try:
    import httpx  # type: ignore
except Exception:
    httpx = None  # type: ignore

try:
    from googleapiclient.errors import HttpError  # type: ignore
except Exception:
    HttpError = None  # type: ignore

try:
    import smtplib  # type: ignore
except Exception:
    smtplib = None  # type: ignore

@dataclass
class ErrorNotice:
    code: str                  # short machine code, e.g. "NETWORK_DNS", "SMTP_AUTH"
    title: str                 # short, user-facing title
    user_message: str          # safe message you can show to users (what to try next)
    hint: Optional[str] = None # optional extra hint
    support_id: str = ""       # unique ID to correlate logs
    debug: Optional[str] = None  # long detail for logs only

    def flash_text(self) -> str:
        base = f"{self.title}: {self.user_message}"
        if self.hint:
            base += f" — {self.hint}"
        base += f" (ref: {self.support_id})"
        return base


def _is_openai_dns_error(exc: Exception) -> bool:
    # Typical chain: httpx.ConnectError -> httpcore.ConnectError -> OSError [Errno 8]
    msg = str(exc)
    return ("nodename nor servname provided" in msg) or ("Name or service not known" in msg)


def _smtp_is_535(exc: Exception) -> bool:
    return hasattr(exc, "smtp_code") and getattr(exc, "smtp_code", None) == 535


def _oauth_redirect_mismatch(exc: Exception) -> bool:
    # You might raise a ValueError/RuntimeError with this substring upstream
    return "redirect_uri_mismatch" in str(exc).lower()


def _gmail_invalid_grant(exc: Exception) -> bool:
    return "invalid_grant" in str(exc).lower()


def _pg_conn_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return ("could not connect to server" in s or "connection refused" in s
            or "ssl" in s and "verify" in s)


def _sqlite_open_error(exc: Exception) -> bool:
    return "sqlite3.OperationalError" in str(type(exc)) and "unable to open database file" in str(exc)


def _is_openai_rate_or_quota(exc: Exception) -> Optional[str]:
    s = str(exc).lower()
    if "rate_limit" in s or "rate limit" in s:
        return "RATE_LIMIT"
    if "quota" in s:
        return "QUOTA"
    return None


def build_error_notice(exc: Exception, context: Optional[Dict[str, Any]] = None) -> ErrorNotice:
    """
    Map raw exceptions to user-safe, helpful notices.
    Keep messages short; add 'hint' for one actionable step.
    """
    ctx = context or {}
    op = ctx.get("op", "operation")
    support_id = uuid.uuid4().hex[:8]

    # 1) VPN/DNS / OpenAI reachability
    if _is_openai_dns_error(exc):
        return ErrorNotice(
            code="NETWORK_DNS",
            title="Network problem",
            user_message="We couldn’t reach OpenAI from this network.",
            hint="If you’re on a VPN or corporate Wi-Fi, disconnect or try another network.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 2) OpenAI rate/quota
    rate_or_quota = _is_openai_rate_or_quota(exc)
    if rate_or_quota == "RATE_LIMIT":
        return ErrorNotice(
            code="OPENAI_RATE",
            title="Busy right now",
            user_message="We’re sending too many requests at once.",
            hint="Please retry in a few seconds.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )
    if rate_or_quota == "QUOTA":
        return ErrorNotice(
            code="OPENAI_QUOTA",
            title="Quota exceeded",
            user_message="We’ve hit our OpenAI usage quota.",
            hint="We’ll restore service soon.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 3) SMTP 535 — bad creds / app password
    if _smtp_is_535(exc):
        return ErrorNotice(
            code="SMTP_AUTH",
            title="Email send failed",
            user_message="The email account rejected our sign-in.",
            hint="Check the SMTP username and app password.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 4) Google OAuth redirect URI mismatch
    if _oauth_redirect_mismatch(exc):
        return ErrorNotice(
            code="GOOGLE_OAUTH_REDIRECT",
            title="Sign-in configuration issue",
            user_message="Google sign-in is blocked by a redirect mismatch.",
            hint="Ask support to update the OAuth redirect URL.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 5) Gmail 'invalid_grant' — token revoked/expired
    if _gmail_invalid_grant(exc):
        return ErrorNotice(
            code="GOOGLE_OAUTH_REFRESH",
            title="Google connection expired",
            user_message="Your Google connection needs to be refreshed.",
            hint="Reconnect Google in Settings.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 6) Database connectivity
    if _pg_conn_error(exc) or _sqlite_open_error(exc):
        return ErrorNotice(
            code="DB_CONN",
            title="Database unavailable",
            user_message="We couldn’t connect to the database.",
            hint="Please try again shortly.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 7) Google API HttpError — surface status, keep message generic
    if HttpError and isinstance(exc, HttpError):
        status = getattr(exc, "status_code", None)
        return ErrorNotice(
            code=f"GOOGLE_API_{status or 'ERR'}",
            title="Google API error",
            user_message="We hit a temporary issue with Google’s API.",
            hint="Please retry later.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 8) httpx timeouts / generic network
    if httpx and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):  # type: ignore
        return ErrorNotice(
            code="NETWORK_TIMEOUT",
            title="Network timeout",
            user_message="A request took too long to respond.",
            hint="Please retry in a moment.",
            support_id=support_id,
            debug=f"{op}: {exc!r}",
        )

    # 9) Fallback catch-all
    return ErrorNotice(
        code="UNKNOWN",
        title="Something went wrong",
        user_message="An unexpected error occurred.",
        hint="Please retry. If it persists, contact support.",
        support_id=support_id,
        debug=f"{op}: {exc!r}",
    )
