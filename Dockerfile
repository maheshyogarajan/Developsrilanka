# ── Stage 1: build deps ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests first for layer-cache efficiency
COPY pyproject.toml uv.lock ./

# Install into /app/.venv (no system install)
RUN uv sync --frozen --no-dev --no-install-project

# ── Stage 2: runtime ───────────────────────────────────────────────────────────
FROM python:3.11-slim

# Install OS-level runtime deps (psycopg2-binary needs libpq, Pillow needs libjpeg)
# postgresql-client-15 added 2026-05-24 (Tier D1/F1) for daily pg_dump backup.
# Debian 12 (bookworm) base ships PG 15 client; works fine against PG 17 server
# for logical pg_dump --format=custom (forward compat is per-format guaranteed
# but we pin server-side flags in tasks/pg_backup.py to be PG15-client-safe).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libjpeg62-turbo \
    libwebp7 \
    postgresql-client-15 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Make the venv python the default
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application source
COPY . .

EXPOSE 8080

# Healthcheck using the /healthz alias added in app.py
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Default process: web (overridden by fly.toml [processes] for worker)
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--timeout", "120", "--access-logfile", "-", "wsgi:app"]
