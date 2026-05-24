"""
Sentry initialization — Tier D1 / E1.

Single entrypoint: ``init_sentry()``. Gated on the ``SENTRY_DSN`` env var so
local dev / test runs without a DSN are silent no-ops. In production
(Fly.io), set the DSN via ``flyctl secrets set SENTRY_DSN=...``.

Configuration:
  * traces_sample_rate=0.1  -> 10% of requests get perf spans (cost-controlled).
  * profiles_sample_rate=0.0 -> profiling off (cost-controlled).
  * send_default_pii=False  -> never ship user PII (emails, IPs, cookies).
  * environment=FLASK_ENV   -> defaults to "production".
  * release=FLY_RELEASE_VERSION -> defaults to "dev" when Fly env not present.

Integrations: Flask + SQLAlchemy. These cover request lifecycle, route
resolution, DB queries, and unhandled exceptions out of the box — no custom
breadcrumb instrumentation needed (per task scope cap).

Returns the truthy DSN string on init, or None when no DSN was set (so the
caller can log "Sentry: disabled (no DSN)" if it wants).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def init_sentry() -> Optional[str]:
    """Initialize Sentry if SENTRY_DSN is set; no-op otherwise.

    Idempotent: calling twice with the same DSN reinitializes the SDK, which
    is harmless. Safe to call from app startup or worker startup.
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry: disabled (SENTRY_DSN not set)")
        return None

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    except ImportError as e:
        # Don't crash the app if the dep is missing — log and continue.
        logger.error("Sentry: sentry-sdk import failed (%s); continuing without it", e)
        return None

    environment = os.environ.get("FLASK_ENV", "production")
    release = os.environ.get("FLY_RELEASE_VERSION") or "dev"

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,    # 10% perf-trace sample
        profiles_sample_rate=0.0,  # profiling off for cost control
        environment=environment,
        release=release,
        send_default_pii=False,
    )
    logger.info(
        "Sentry: initialized (environment=%s, release=%s, traces=10%%)",
        environment, release,
    )
    return dsn


__all__ = ["init_sentry"]
