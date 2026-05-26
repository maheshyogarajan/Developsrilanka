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
  * before_send             -> healthz-event drop + auth-user-id attribution.

Integrations: Flask + SQLAlchemy. These cover request lifecycle, route
resolution, DB queries, and unhandled exceptions out of the box — no custom
breadcrumb instrumentation needed (per task scope cap).

Returns the truthy DSN string on init, or None when no DSN was set (so the
caller can log "Sentry: disabled (no DSN)" if it wants).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health-check URL prefixes Sentry should never ingest. These endpoints are
# probed by Fly + uptime monitors at high frequency and represent zero signal
# even when they 5xx (we'd see the deploy failure other ways).
# ---------------------------------------------------------------------------
_HEALTHCHECK_PATHS = (
    "/healthz",
    "/health",
)


def _event_request_url(event: Dict[str, Any]) -> str:
    """Pull the request URL out of a Sentry event, tolerant of missing keys."""
    request = event.get("request") or {}
    return request.get("url") or ""


def _is_healthcheck_event(event: Dict[str, Any]) -> bool:
    """True if the event is for a /healthz or /health request."""
    url = _event_request_url(event)
    if not url:
        return False
    # Defend against absolute URLs, query strings, and trailing slashes.
    # Match the path component only.
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path or url
    except Exception:
        path = url
    path = path.rstrip("/") or "/"
    for prefix in _HEALTHCHECK_PATHS:
        prefix_norm = prefix.rstrip("/") or "/"
        if path == prefix_norm or path.startswith(prefix_norm + "/"):
            return True
    return False


def _attach_authenticated_user_id(event: Dict[str, Any]) -> None:
    """If a user is logged in via Flask-Login, attach only their numeric ID.

    Never email, never NIC, never any PII — only the user.id. If the request
    is anonymous OR if anything goes wrong inspecting current_user, leave the
    user section as Sentry built it (which, with send_default_pii=False, is
    already empty of identifying fields).
    """
    try:
        # Lazy import to avoid pulling Flask into non-Flask test contexts.
        from flask_login import current_user  # type: ignore
    except Exception:
        return

    try:
        is_anon_attr = getattr(current_user, "is_anonymous", True)
        # current_user may be a LocalProxy outside a request context — in
        # that case attribute access raises; the outer try/except handles it.
        is_anon = bool(is_anon_attr() if callable(is_anon_attr) else is_anon_attr)
    except Exception:
        return

    if is_anon:
        return

    try:
        user_id = getattr(current_user, "id", None)
    except Exception:
        return

    if user_id is None:
        return

    user_section = dict(event.get("user") or {})
    # Only the numeric/string id. Strip everything else that might have been
    # auto-populated (e.g. email, username) even though send_default_pii=False
    # should already have done that.
    user_section["id"] = str(user_id)
    user_section.pop("email", None)
    user_section.pop("username", None)
    user_section.pop("ip_address", None)
    event["user"] = user_section


def _before_send(event: Dict[str, Any], hint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Sentry before_send hook.

    Returns None to drop the event (healthcheck noise), or the (possibly
    enriched) event dict to forward to Sentry.
    """
    if _is_healthcheck_event(event):
        return None
    _attach_authenticated_user_id(event)
    return event


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
        before_send=_before_send,
    )
    logger.info(
        "Sentry: initialized (environment=%s, release=%s, traces=10%%, "
        "healthz_filter=on, auth_user_id=on)",
        environment, release,
    )
    return dsn


__all__ = ["init_sentry"]
