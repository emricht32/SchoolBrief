# app/main.py
import os
from pathlib import Path
from fastapi import FastAPI, Request, Query, Header, HTTPException

from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware 

# Only load .env locally (don’t rely on it in Cloud Run)
if os.getenv("K_SERVICE") is None:  # not on Cloud Run
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("Loaded .env for local dev")
    except Exception:
        pass

from .db import init_db
from .session import add_session_middleware
from .auth import router as auth_router
from .views import router as views_router
from .billing import router as billing_router
from .scheduler import start_scheduler
from .scheduler import tick as scheduler_tick
from .errors import build_error_notice
from .logger import logger 

# --- Base URL & redirect URI helpers ---
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_REDIRECT = f"{PUBLIC_BASE_URL}/auth/google/callback"
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", DEFAULT_REDIRECT)

app = FastAPI()
add_session_middleware(app)  # ensure this sets Secure cookies in prod (https_only=True)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# CORS from env (comma-separated). "*" allowed if you really want.
origins_env = os.getenv("ALLOWED_ORIGINS", "*")
origins = [o.strip() for o in origins_env.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Robust static path (works in container)
static_dir = Path(__file__).resolve().parents[1] / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(views_router, tags=["views"])
app.include_router(billing_router, prefix="/billing", tags=["billing"])

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Classify & log
    notice = build_error_notice(exc, {"op": "global"})
    logger.error(f"[{notice.code}] {notice.debug} (ref={notice.support_id})")

    # Prefer to redirect with flash for UI routes; JSON for API paths
    path = request.url.path or "/"
    if path.startswith("/api/"):
        return JSONResponse(
            {"error": notice.title, "message": notice.user_message, "ref": notice.support_id, "code": notice.code},
            status_code=500,
        )
    flash = notice.flash_text()
    return RedirectResponse(f"/app?flash={flash}", status_code=303)

@app.on_event("startup")
def check_env():
    # Adjust these names to your actual env var names
    must = ["SESSION_SECRET", "OPENAI_API_KEY", "DATABASE_URL", "CRON_TOKEN", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    missing = [k for k in must if not os.getenv(k)]
    if missing:
        logger.debug(f"⚠️ Missing env vars: {', '.join(missing)}")  # log, don’t crash

    # Log effective OAuth redirect (helps catch mismatch vs Google Console)
    logger.debug(f"🔐 OAuth redirect URI = {GOOGLE_OAUTH_REDIRECT_URI}")

    # Warn if running https domain but redirect isn’t https
    if PUBLIC_BASE_URL.startswith("https://") and not GOOGLE_OAUTH_REDIRECT_URI.startswith("https://"):
        logger.debug("⚠️ PUBLIC_BASE_URL is https, but GOOGLE_OAUTH_REDIRECT_URI is not https. Update it.")

@app.on_event("startup")
def on_startup():
    init_db()
    # Don’t run in-process schedulers on Cloud Run; use Cloud Scheduler hitting your endpoints.
    if os.getenv("ENABLE_INPROC_SCHEDULER") == "1":
        start_scheduler()

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/favicon.ico")
def favicon():
    return RedirectResponse("/static/favicon.ico")

# Debug endpoint to confirm what redirect the service is using (remove in prod if you want)
@app.get("/auth/redirect-debug")
def oauth_redirect_debug():
    data = {
        "public_base_url": PUBLIC_BASE_URL,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "https_expected": PUBLIC_BASE_URL.startswith("https://"),
    }
    return JSONResponse(data)

# Cloud Scheduler trigger (set CRON_TOKEN env var; pass ?token= or X-Cron-Token header)

@app.post("/cron/tick")
def cron_tick(
    token: str | None = Query(None),
    x_cron_token: str | None = Header(None),
    force: bool = Query(False),   # ← FastAPI/Pydantic parses true/false/1/0/yes/no
):
    expected = os.getenv("CRON_TOKEN")
    provided = token or x_cron_token
    if not expected or provided != expected:
        raise HTTPException(status_code=401, detail="unauthorized")

    count = scheduler_tick(force=force)
    return {"triggered": count, "force": force}
