# ---------- base ----------
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Optional: set your default TZ to keep logs consistent (override at deploy if needed)
ENV TZ=UTC

# Install minimal OS deps (add more here only if you truly need them)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---------- deps layer ----------
FROM base AS deps

# Upgrade pip and install deps first to leverage Docker cache
COPY requirements.txt .
RUN python -m pip install --upgrade pip wheel \
 && pip install --no-cache-dir -r requirements.txt

# ---------- app layer ----------
FROM base AS app

# Create non-root user (good practice for Cloud Run)
RUN useradd -m -u 1001 appuser
USER appuser

# Copy installed site-packages from deps image
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copy project
WORKDIR /app
COPY . .

# Ensure imports like "from app.x import ..." resolve
ENV PYTHONPATH=/app

# Default ASGI module (can be overridden with APP_MODULE env)
ENV APP_MODULE=app.main:app

# Expose is optional on Cloud Run, but harmless for local dev
EXPOSE 8080

# Use a simple, robust entrypoint
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
