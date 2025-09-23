# app/db.py
import os
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base


def _mask_creds(url: str) -> str:
    """Hide credentials for safe logging."""
    try:
        parts = urlsplit(url)
        if parts.username or parts.password:
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return url


def _in_cloud_run() -> bool:
    # Cloud Run sets these
    return bool(os.getenv("K_SERVICE") or os.getenv("K_REVISION") or os.getenv("K_CONFIGURATION"))


def _sqlite_default_url() -> str:
    # In Cloud Run, only /tmp is writable.
    if _in_cloud_run():
        return "sqlite:////tmp/schoolbrief.db"
    return "sqlite:///./schoolbrief.db"


def _resolve_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        return _sqlite_default_url()

    # Ensure TLS for Postgres (Supabase, etc.)
    if url.startswith("postgresql://") and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    # Prefer psycopg v3 if installed
    try:
        import psycopg  # noqa: F401
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    except Exception:
        # fall back to whichever driver SQLAlchemy finds (psycopg2/pg8000/etc.)
        pass

    return url


DATABASE_URL = _resolve_url()

# Optional: print effective DB target once on boot (without creds)
if os.getenv("LOG_DB_URL") == "1":
    print("DB ->", _mask_creds(DATABASE_URL))

# Serverless-friendly engine settings
engine_kwargs = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,   # drop dead/idle connections automatically
}

if DATABASE_URL.startswith("sqlite"):
    # Needed for SQLite in threaded servers
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Conservative pool for Cloud Run / pgBouncer / Supabase pooled port
    # (Adjust if you see connection pressure)
    engine_kwargs.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "5")),
        pool_recycle=180,   # recycle connections periodically
        pool_timeout=30,
    )

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def init_db():
    """Create tables if they don't exist. Use Alembic for real migrations."""
    Base.metadata.create_all(bind=engine)
