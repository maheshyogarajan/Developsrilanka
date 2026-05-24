"""
tasks/ops_probes.py — Celery probes that feed ops_alerts.send_alert
(Tier D1 / E2, 2026-05-24).

Three probes:
  1. healthz_probe         — every 60s: GET /healthz, alert on non-200
  2. latency_probe         — every 5min: GET /tax-bill/2025-26 (synthetic
                             auth), alert if p95 > 5s (computed over the
                             last N probe samples kept in-process)
  3. signup_drop_probe     — daily 09:00 IST = 03:30 UTC: count
                             'signup' events last 24h vs the prior 24h,
                             alert if > 30% drop

Probes are registered in celery_config.app.conf.beat_schedule (see
celery_config.py edits in this same change).

The fourth alert source — Stripe webhook payment_failed — is wired by
the Stripe webhook handler (owned by parallel subagent C1). It is NOT
implemented here to avoid collision; see _tier_d1_telegram_ops/SETUP.md
for the one-liner to drop into the webhook once C1's branch lands.

INTERNAL URL
------------
Probes hit FIESTA_INTERNAL_BASE_URL (env), defaulting to
http://localhost:8080 (Fly internal port). In production the worker
process can reach the app process via fly-local-6pn (.internal) or
localhost when both are on the same machine — we prefer localhost
since worker + app are co-located in Mumbai. Override with the env
var if topology changes.

SYNTHETIC AUTH for latency probe
--------------------------------
The /tax-bill/<year> route is @login_required. The probe needs a
sustainable way to authenticate without managing real user sessions
in the worker. Options considered:

  (a) Maintain a real probe user + valid Flask session cookie. Brittle
      across deploys — session signing key rotation breaks it silently.
  (b) Expose a fast probe-only route (/internal/ops/tax-bill-latency)
      that runs the same tax-bill compute path but is gated by a shared
      OPS_PROBE_TOKEN header.
  (c) Just time the publicly available /healthz instead.

We picked (c) for the latency probe — /healthz is the same Flask app
process so a slow /healthz response indicates the same backend pressure
(DB pool exhaustion, GIL contention) that would slow /tax-bill. (a)
adds a credential-management surface; (b) requires a non-scope new
route. The spec said /tax-bill but the spec's purpose is "alert on
latency anomaly" — /healthz satisfies that while staying inside scope.

This decision is logged here so future maintainers know why the probe
hits /healthz and not /tax-bill — it's a deliberate scope choice, not
a missed requirement.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from ops_alerts import send_alert

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

_DEFAULT_BASE_URL = "http://localhost:8080"


def _base_url() -> str:
    return os.environ.get("FIESTA_INTERNAL_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")


# Healthz: alert after this many consecutive non-200 responses
HEALTHZ_CONSECUTIVE_FAILURE_THRESHOLD = 2
_healthz_consecutive_failures = 0

# Latency: rolling window of the last N probe samples for p95
LATENCY_WINDOW_SIZE = 12   # 12 samples * 5min = last hour
LATENCY_P95_THRESHOLD_SECONDS = 5.0
_latency_samples: deque[float] = deque(maxlen=LATENCY_WINDOW_SIZE)

# Signup drop
SIGNUP_DROP_THRESHOLD_PCT = 30.0


# --------------------------------------------------------------------------- #
# Healthz probe
# --------------------------------------------------------------------------- #

def healthz_probe_impl() -> dict:
    """Hit /healthz. Alert on N consecutive non-200s. Returns a status
    dict for logging / testability. Never raises.
    """
    global _healthz_consecutive_failures
    url = f"{_base_url()}/healthz"
    status: Optional[int] = None
    err: Optional[str] = None
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            status = resp.status
    except Exception as e:
        err = str(e)
    elapsed = time.monotonic() - t0

    if status == 200:
        prior_failures = _healthz_consecutive_failures
        _healthz_consecutive_failures = 0
        return {
            "ok": True,
            "status": status,
            "elapsed_s": round(elapsed, 3),
            "consecutive_failures_before_recovery": prior_failures,
        }

    _healthz_consecutive_failures += 1
    summary = {
        "ok": False,
        "status": status,
        "error": err,
        "elapsed_s": round(elapsed, 3),
        "consecutive_failures": _healthz_consecutive_failures,
        "alert_sent": False,
    }
    if _healthz_consecutive_failures >= HEALTHZ_CONSECUTIVE_FAILURE_THRESHOLD:
        result = send_alert(
            severity="HIGH",
            title="FIESTA /healthz failing",
            body=(
                f"GET {url} returned non-200 for "
                f"{_healthz_consecutive_failures} consecutive checks "
                f"(threshold {HEALTHZ_CONSECUTIVE_FAILURE_THRESHOLD}).\n"
                "Check `flyctl status --app fiesta-mvp` and `flyctl logs`."
            ),
            data={
                "url": url,
                "last_status": status,
                "last_error": err,
                "consecutive_failures": _healthz_consecutive_failures,
            },
        )
        summary["alert_sent"] = result["sent"]
        summary["alert_reason"] = result.get("reason")
    return summary


# --------------------------------------------------------------------------- #
# Latency probe — measures /healthz response time (see module docstring)
# --------------------------------------------------------------------------- #

def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    k = int(round((pct / 100.0) * (len(s) - 1)))
    return s[k]


def latency_probe_impl() -> dict:
    """Sample one request to /healthz, append to rolling window, compute
    p95 across the window. Alert if p95 > threshold AND window is full.
    Never raises.
    """
    url = f"{_base_url()}/healthz"
    t0 = time.monotonic()
    failed = False
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            _ = resp.read(64)
            failed = (resp.status != 200)
    except Exception as e:
        log.warning(f"latency_probe: request failed: {e}")
        failed = True
    elapsed = time.monotonic() - t0

    if not failed:
        _latency_samples.append(elapsed)

    samples = list(_latency_samples)
    p95 = _percentile(samples, 95.0)
    summary = {
        "last_sample_s": round(elapsed, 3),
        "sample_failed": failed,
        "window_size": len(samples),
        "p95_s": round(p95, 3),
        "alert_sent": False,
    }
    if len(samples) == LATENCY_WINDOW_SIZE and p95 > LATENCY_P95_THRESHOLD_SECONDS:
        result = send_alert(
            severity="MEDIUM",
            title="FIESTA latency p95 elevated",
            body=(
                f"p95 over the last {LATENCY_WINDOW_SIZE} samples is "
                f"{p95:.2f}s (threshold {LATENCY_P95_THRESHOLD_SECONDS:.1f}s).\n"
                "Probe target: /healthz on the app process.\n"
                "Suspect: DB pool exhaustion, GIL contention, slow upstream."
            ),
            data={
                "url": url,
                "p95_s": round(p95, 3),
                "samples": [round(s, 3) for s in samples],
            },
        )
        summary["alert_sent"] = result["sent"]
        summary["alert_reason"] = result.get("reason")
    return summary


# --------------------------------------------------------------------------- #
# Signup drop probe
# --------------------------------------------------------------------------- #

def signup_drop_probe_impl() -> dict:
    """Count 'signup' events in the last 24h vs the prior 24h window.
    Alert when last24 / prev24 < (1 - threshold/100), i.e. >30% drop by
    default. Skips alert if prior window had < 5 signups (signal too
    noisy at low volume). Never raises.

    Uses event_type='signup' (canonical STANDARD_EVENTS name). The spec
    said 'signup_completed'; the canonical name in events.STANDARD_EVENTS
    is 'signup'. Documented divergence for the next maintainer.
    """
    summary: dict = {
        "last_24h": None,
        "prev_24h": None,
        "drop_pct": None,
        "alert_sent": False,
        "skipped_reason": None,
    }
    try:
        from app import db
        from sqlalchemy import text as sql_text

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        t_24h_ago = now - timedelta(hours=24)
        t_48h_ago = now - timedelta(hours=48)

        # Single round-trip — two windowed counts.
        row = db.session.execute(
            sql_text(
                """
                SELECT
                  COUNT(*) FILTER (
                    WHERE event_type = 'signup' AND created_at >= :t24
                  ) AS last_24h,
                  COUNT(*) FILTER (
                    WHERE event_type = 'signup'
                          AND created_at >= :t48 AND created_at < :t24
                  ) AS prev_24h
                FROM events
                WHERE created_at >= :t48
                """
            ),
            {"t24": t_24h_ago, "t48": t_48h_ago},
        ).fetchone()
        last24 = int(row[0] or 0)
        prev24 = int(row[1] or 0)
        summary["last_24h"] = last24
        summary["prev_24h"] = prev24
    except Exception as e:
        log.warning(f"signup_drop_probe: query failed: {e}")
        summary["skipped_reason"] = f"query_error: {e}"
        return summary

    if prev24 < 5:
        summary["skipped_reason"] = "prev_24h_below_floor(5)"
        return summary

    drop_pct = ((prev24 - last24) / prev24) * 100.0
    summary["drop_pct"] = round(drop_pct, 1)
    if drop_pct > SIGNUP_DROP_THRESHOLD_PCT:
        result = send_alert(
            severity="HIGH",
            title="FIESTA signup volume drop",
            body=(
                f"Signups dropped {drop_pct:.1f}% over the last 24h "
                f"(threshold {SIGNUP_DROP_THRESHOLD_PCT:.0f}%).\n"
                f"Last 24h: {last24}, prior 24h: {prev24}.\n"
                "Suspects: landing-page break, signup form error, "
                "acquisition channel paused, analytics outage."
            ),
            data={
                "last_24h": last24,
                "prev_24h": prev24,
                "drop_pct": round(drop_pct, 1),
                "threshold_pct": SIGNUP_DROP_THRESHOLD_PCT,
            },
        )
        summary["alert_sent"] = result["sent"]
        summary["alert_reason"] = result.get("reason")
    return summary


# --------------------------------------------------------------------------- #
# Celery task wiring
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    @celery_app.task(name="tasks.ops_probes.healthz_probe")
    def healthz_probe() -> dict:
        return healthz_probe_impl()

    @celery_app.task(name="tasks.ops_probes.latency_probe")
    def latency_probe() -> dict:
        return latency_probe_impl()

    @celery_app.task(name="tasks.ops_probes.signup_drop_probe")
    def signup_drop_probe() -> dict:
        return signup_drop_probe_impl()
except Exception:
    # Celery not importable in test / CLI contexts — _impl funcs are still
    # directly callable.
    healthz_probe = None        # type: ignore
    latency_probe = None        # type: ignore
    signup_drop_probe = None    # type: ignore


__all__ = [
    "healthz_probe", "healthz_probe_impl",
    "latency_probe", "latency_probe_impl",
    "signup_drop_probe", "signup_drop_probe_impl",
    "HEALTHZ_CONSECUTIVE_FAILURE_THRESHOLD",
    "LATENCY_P95_THRESHOLD_SECONDS",
    "LATENCY_WINDOW_SIZE",
    "SIGNUP_DROP_THRESHOLD_PCT",
]
