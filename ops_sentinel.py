"""
Ops Sentinel — self-monitoring + auto-incident response (Wave 2.4, 2026-05-17).

Council #2 sequenced this immediately after the Event Spine because it's what
makes "CEO is observer, not operator" possible: the system has to catch its
own fires before a human gets paged. Without it, every CBSL outage, Neon
blip, Celery queue backup, or Gemini cost spike is silent until someone
notices a downstream symptom hours later.

DESIGN INTENT
-------------
* SIX health checks (CBSL freshness, Fly /healthz, Neon, Celery queue depth,
  Gemini cost 24h, false-ready spike) run every 5 minutes via a Celery beat
  schedule entry.
* Each check returns a uniform dict — {healthy, value, threshold, message} —
  so the dispatcher can format alerts without knowing what the check does.
* Failures dispatch an alert: today an `Event(event_type='ops_alert')` row +
  log line; tomorrow a SendGrid email to ops@smarter.tax (wiring stub below;
  flip ENABLE_SENDGRID_ALERTS=true once the rate-limit/dedup story lands).
* DO NOT page Telegram — FIESTA has no Telegram integration (council #2
  explicit). Telegram is a CEO-OS surface, not a FIESTA surface.
* log_gemini_cost() is the public helper every Gemini-touching surface
  (CRM classifier, AI Support, remittance_import) calls after each API call.
  Cost is estimated from the published price table (PRICE_TABLE below).

BEAT SCHEDULE
-------------
The orchestrator wires this into celery_config.app.conf.beat_schedule:

    'ops_sentinel-every-5min': {
        'task': 'ops_sentinel.run_and_alert',
        'schedule': crontab(minute='*/5'),
    },

ROUTE REGISTRATION
------------------
The orchestrator wires ops_routes.register_routes(app) into main.py alongside
the other register_routes() calls.

PUBLIC API
----------
    from ops_sentinel import run_all_checks, dispatch_alert, log_gemini_cost
    snapshot = run_all_checks()
    log_gemini_cost(user_id=42, model_name='gemini-2.5-flash',
                    prompt_tokens=1200, completion_tokens=450,
                    source='remittance_import')

FOLLOW-ON HARDENING (called out for orchestrator)
-------------------------------------------------
1. Sentry SDK init in app.py so unhandled exceptions in checks land in Sentry.
2. PagerDuty Events API integration in dispatch_alert (off-hours escalation).
3. Auto-rollback on failed deploy: tie this to fly.toml release_command + a
   /healthz canary that flips a feature flag back to the previous release.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# CBSL scraper freshness threshold. The CBSL scraper is invoked on-demand via
# fx_rate_service (not on a cron), so "fresh" really means "we've talked to
# CBSL at least once in the last day". 25h gives a buffer over the 24h
# weekend/holiday gap where no rates are published.
CBSL_STALE_HOURS = 25

# Fly.io public healthcheck. Read from FLY_APP_URL env if set; falls back to
# the production hostname. 5s timeout — Fly's /healthz must be fast or it's
# already in trouble.
FLY_HEALTHZ_URL_DEFAULT = "https://fiesta-mvp.fly.dev/healthz"
FLY_HEALTHZ_TIMEOUT_S = 5

# Celery queue depth alert threshold. The db-broker `kombu_message` table
# holds pending Celery messages; >100 backed up means workers are falling
# behind real time and a backlog is building.
CELERY_QUEUE_ALERT_THRESHOLD = 100

# Gemini cost ceiling — placeholder $10/day. Refine after we have real volume
# data (Wave 2.3 CRM recompute + Wave 3.2 AI support will dominate the bill).
GEMINI_DAILY_COST_USD_CEILING = Decimal("10.00")

# False-ready spike = how many times in the last 24h has
# ird_ready_staff_reviewed been flipped from True back to False (an active
# regression — Lanka.tax staff un-marked something). 1+ is unusual.
FALSE_READY_ALERT_THRESHOLD = 1

# Stripe webhook delivery health (v1.0 — Gemini R1 Q6.2 finding).
# Healthy if the last STRIPE_WEBHOOK_WINDOW events have failure rate
# <= STRIPE_WEBHOOK_FAILURE_RATE. The window must contain at least
# STRIPE_WEBHOOK_MIN_EVENTS events before the check fires — otherwise the
# first webhook failure on a fresh install would page on a 1/1 sample.
STRIPE_WEBHOOK_WINDOW = 20            # last 20 events considered
STRIPE_WEBHOOK_MIN_EVENTS = 5         # below this the check stays "healthy: unknown"
STRIPE_WEBHOOK_FAILURE_RATE = 0.50    # > 50% failure in window = alert

# Gemini price table — USD per 1M tokens (input, output). From Google AI pricing
# page captured 2026-05-17. Pricing changes; refresh quarterly. Keys are
# matched by prefix (case-insensitive) — "gemini-2.5-flash-preview" matches
# "gemini-2.5-flash" entry.
#
# Source: https://ai.google.dev/pricing (snapshot 2026-05-17)
PRICE_TABLE: Dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.5-flash": (Decimal("0.075"), Decimal("0.30")),
    "gemini-2.5-pro":   (Decimal("1.25"),  Decimal("5.00")),
    "gemini-1.5-flash": (Decimal("0.075"), Decimal("0.30")),
    "gemini-1.5-pro":   (Decimal("1.25"),  Decimal("5.00")),
    "gemini-pro":       (Decimal("1.25"),  Decimal("5.00")),
}
# Conservative fallback when we don't recognise the model name. Use the
# flash-tier price so we under-bill rather than over-bill (a missing model
# name is more likely a flash variant than a pro one in practice).
_DEFAULT_PRICE = (Decimal("0.075"), Decimal("0.30"))


# --------------------------------------------------------------------------- #
# Cost estimation helper
# --------------------------------------------------------------------------- #

def _estimate_cost_usd(model_name: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Return USD cost estimate as Decimal with 6 decimal precision.

    Per-million-token pricing → cost = (in_tokens * in_price + out_tokens * out_price) / 1_000_000.
    Unknown model names fall back to flash-tier pricing.
    """
    if prompt_tokens is None:
        prompt_tokens = 0
    if completion_tokens is None:
        completion_tokens = 0
    name = (model_name or "").lower().strip()
    in_price, out_price = _DEFAULT_PRICE
    for key, prices in PRICE_TABLE.items():
        if name.startswith(key):
            in_price, out_price = prices
            break
    cost = (
        Decimal(prompt_tokens) * in_price
        + Decimal(completion_tokens) * out_price
    ) / Decimal(1_000_000)
    # Quantize to 6 decimal places — matches the NUMERIC(10,6) column.
    return cost.quantize(Decimal("0.000001"))


# --------------------------------------------------------------------------- #
# log_gemini_cost — public helper called by every Gemini-touching surface
# --------------------------------------------------------------------------- #

def log_gemini_cost(
    user_id: Optional[int],
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    source: str,
) -> Optional[int]:
    """Best-effort insert one GeminiCostLog row. Returns the new row id on
    success, None on any failure. NEVER raises.

    Mirrors events.emit() semantics: cost tracking is observational, not
    transactional. A failed cost log MUST NOT break the Gemini caller's
    user-facing flow.

    Args:
        user_id: FK user.id. Nullable — system-internal calls (cron, ad-hoc
                 backfills) pass None.
        model_name: Gemini model SKU, e.g. 'gemini-2.5-flash'.
        prompt_tokens: input token count from the SDK usage_metadata.
        completion_tokens: output token count from the SDK usage_metadata.
        source: short slug — 'remittance_import' | 'ai_support' |
                'crm_recompute' | 'manual' | ...

    Returns:
        The new GeminiCostLog.id on success, None on failure.
    """
    try:
        # Local imports — avoid a circular if app.py ever imports ops_sentinel
        # at module load.
        from app import db
        from gemini_cost_log_model import GeminiCostLog

        cost = _estimate_cost_usd(model_name, prompt_tokens, completion_tokens)

        row = GeminiCostLog(
            user_id=user_id,
            model_name=(model_name or "")[:64],
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            estimated_cost_usd=cost,
            source=(source or "")[:64] if source else None,
        )
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception as exc:
        log.warning(
            "ops_sentinel.log_gemini_cost(model=%r, source=%r) failed: %s. Caller continues.",
            model_name, source, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------- #
# Health checks — each returns the uniform dict
# --------------------------------------------------------------------------- #
#
# Shape: {"healthy": bool, "value": Any, "threshold": Any, "message": str}
#
# Each check MUST swallow its own exceptions and convert them into an
# unhealthy result (run_all_checks wraps with belt-and-braces try/except too,
# but checks should ideally fail in-place with a useful message).
# --------------------------------------------------------------------------- #

def check_cbsl_scraper_fresh() -> Dict[str, Any]:
    """Healthy if the cbsl_rates cache has at least one row from source='cbsl'
    written in the last CBSL_STALE_HOURS hours.

    A stale check here usually means either (a) CBSL website is down + we've
    been falling through to ecb_proxy, or (b) no user has triggered an FX
    lookup in 24h (less concerning, but worth knowing).
    """
    try:
        from sqlalchemy import text
        from app import db
        row = db.session.execute(text("""
            SELECT MAX(fetched_at) AS last_fetch
              FROM cbsl_rates
             WHERE source = 'cbsl'
        """)).fetchone()
        last_fetch = row[0] if row else None
        threshold = f"<= {CBSL_STALE_HOURS}h"
        if last_fetch is None:
            return {
                "healthy": False,
                "value": None,
                "threshold": threshold,
                "message": "no cbsl_rates rows with source='cbsl' ever — scraper has never succeeded",
            }
        age_hours = (datetime.utcnow() - last_fetch).total_seconds() / 3600
        healthy = age_hours <= CBSL_STALE_HOURS
        return {
            "healthy": healthy,
            "value": f"{age_hours:.1f}h",
            "threshold": threshold,
            "message": (
                f"CBSL cache last refreshed {age_hours:.1f}h ago"
                if healthy
                else f"CBSL cache STALE: last refresh {age_hours:.1f}h ago (>{CBSL_STALE_HOURS}h)"
            ),
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": f"<= {CBSL_STALE_HOURS}h",
            "message": f"check raised: {exc}",
        }


def check_fly_healthz() -> Dict[str, Any]:
    """Healthy if GET <FLY_HEALTHZ_URL> returns HTTP 200 within FLY_HEALTHZ_TIMEOUT_S."""
    import os
    url = os.environ.get("FLY_APP_URL")
    if url:
        url = url.rstrip("/") + "/healthz"
    else:
        url = FLY_HEALTHZ_URL_DEFAULT
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "FIESTA-OpsSentinel/1.0"},
        )
        with urllib.request.urlopen(req, timeout=FLY_HEALTHZ_TIMEOUT_S) as resp:
            status = resp.getcode()
        healthy = 200 <= status < 300
        return {
            "healthy": healthy,
            "value": status,
            "threshold": "2xx",
            "message": (
                f"Fly /healthz returned HTTP {status} from {url}"
                if healthy
                else f"Fly /healthz returned HTTP {status} from {url}"
            ),
        }
    except urllib.error.HTTPError as e:
        return {
            "healthy": False,
            "value": e.code,
            "threshold": "2xx",
            "message": f"Fly /healthz HTTP {e.code} from {url}: {e.reason}",
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": "2xx",
            "message": f"Fly /healthz unreachable from {url}: {exc}",
        }


def check_neon_connection() -> Dict[str, Any]:
    """Healthy if `SELECT 1` against the configured DATABASE_URL returns 1."""
    try:
        from sqlalchemy import text
        from app import db
        result = db.session.execute(text("SELECT 1")).scalar()
        healthy = result == 1
        return {
            "healthy": healthy,
            "value": result,
            "threshold": 1,
            "message": "Neon SELECT 1 OK" if healthy else f"Neon SELECT 1 returned {result!r}",
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": 1,
            "message": f"Neon connection failed: {exc}",
        }


def check_celery_queue_depth() -> Dict[str, Any]:
    """Healthy if pending Celery message count <= CELERY_QUEUE_ALERT_THRESHOLD.

    On the db broker (`sqla+postgresql://...`) the queue lives in
    `kombu_message` (confirmed by bin/sanity output, 2026-05-16: present in
    the 53-table neon snapshot). On a redis broker this check returns
    healthy with a message explaining redis isn't queryable here — that's
    a deliberate degradation: the check exists primarily for the db broker
    case where queue blowup is a real failure mode.
    """
    try:
        from sqlalchemy import text
        from app import db
        # The kombu_message table has a `visible` flag in some Kombu versions.
        # Use a defensive query that counts everything — over-counting by a
        # few delivered-but-not-acked rows is fine for an alert threshold of 100.
        row = db.session.execute(text("""
            SELECT COUNT(*) AS depth FROM kombu_message
        """)).fetchone()
        depth = int(row[0]) if row else 0
        healthy = depth <= CELERY_QUEUE_ALERT_THRESHOLD
        return {
            "healthy": healthy,
            "value": depth,
            "threshold": CELERY_QUEUE_ALERT_THRESHOLD,
            "message": (
                f"Celery queue depth = {depth}"
                if healthy
                else f"Celery queue BACKED UP: {depth} pending messages "
                     f"(threshold {CELERY_QUEUE_ALERT_THRESHOLD})"
            ),
        }
    except Exception as exc:
        # No kombu_message table → likely running on redis broker. Degrade
        # gracefully: report healthy with a diagnostic message rather than
        # firing an alert for a deployment that's actually fine.
        msg = str(exc)
        if "kombu_message" in msg or "does not exist" in msg.lower():
            return {
                "healthy": True,
                "value": "n/a",
                "threshold": CELERY_QUEUE_ALERT_THRESHOLD,
                "message": "kombu_message table not present (likely redis broker) — depth check skipped",
            }
        return {
            "healthy": False,
            "value": None,
            "threshold": CELERY_QUEUE_ALERT_THRESHOLD,
            "message": f"Celery queue depth check failed: {exc}",
        }


def check_gemini_cost_24h() -> Dict[str, Any]:
    """Healthy if SUM(estimated_cost_usd) over last 24h is <= ceiling."""
    try:
        from sqlalchemy import text
        from app import db
        cutoff = datetime.utcnow() - timedelta(hours=24)
        row = db.session.execute(text("""
            SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total
              FROM gemini_cost_log
             WHERE created_at >= :cutoff
        """), {"cutoff": cutoff}).fetchone()
        total = Decimal(row[0]) if row and row[0] is not None else Decimal("0")
        healthy = total <= GEMINI_DAILY_COST_USD_CEILING
        return {
            "healthy": healthy,
            "value": f"${total:.4f}",
            "threshold": f"<= ${GEMINI_DAILY_COST_USD_CEILING}",
            "message": (
                f"Gemini 24h spend = ${total:.4f}"
                if healthy
                else f"Gemini spend BLOWN BUDGET: ${total:.4f} in last 24h "
                     f"(ceiling ${GEMINI_DAILY_COST_USD_CEILING})"
            ),
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": f"<= ${GEMINI_DAILY_COST_USD_CEILING}",
            "message": f"Gemini cost check failed: {exc}",
        }


def check_false_ready_spike() -> Dict[str, Any]:
    """Healthy if <= FALSE_READY_ALERT_THRESHOLD remittance_entry rows had
    ird_ready_staff_reviewed flipped from True → False in the last 24h.

    Reads `audit_log` (the existing 3,078-row table the remittance routes
    write to via _audit() in remittance_routes.py). A True → False flip is
    Lanka.tax staff un-marking a remittance as IRD-ready, which usually
    signals either (a) staff caught a regression after marking it ready, or
    (b) our state model is letting things flip back too freely. Either way,
    1+ is worth a look.

    The changed_fields JSON column carries {field_name: {old: ..., new: ...}}
    per AuditLog docstring. We probe the JSON for ird_ready_staff_reviewed
    with old=true new=false. Uses PostgreSQL JSON operators (->) and is
    deliberately tolerant of variant key spellings ("old_value"/"new_value"
    seen in some legacy rows).
    """
    try:
        from sqlalchemy import text
        from app import db
        cutoff = datetime.utcnow() - timedelta(hours=24)
        # Tolerate both {old, new} (current) and {old_value, new_value} (legacy)
        # by counting either shape. JSON path errors return NULL so the OR is
        # safe against either schema being absent.
        row = db.session.execute(text("""
            SELECT COUNT(*) FROM audit_log
             WHERE entity_type = 'remittance_entry'
               AND timestamp >= :cutoff
               AND (
                    (changed_fields::jsonb #>> '{ird_ready_staff_reviewed,old}' = 'true'
                     AND changed_fields::jsonb #>> '{ird_ready_staff_reviewed,new}' = 'false')
                 OR (changed_fields::jsonb #>> '{ird_ready_staff_reviewed,old_value}' = 'true'
                     AND changed_fields::jsonb #>> '{ird_ready_staff_reviewed,new_value}' = 'false')
               )
        """), {"cutoff": cutoff}).fetchone()
        count = int(row[0]) if row else 0
        healthy = count <= FALSE_READY_ALERT_THRESHOLD
        return {
            "healthy": healthy,
            "value": count,
            "threshold": f"<= {FALSE_READY_ALERT_THRESHOLD}",
            "message": (
                f"False-ready flips in last 24h = {count}"
                if healthy
                else f"False-ready SPIKE: {count} ird_ready→not-ready flips in last 24h "
                     f"(threshold {FALSE_READY_ALERT_THRESHOLD})"
            ),
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": f"<= {FALSE_READY_ALERT_THRESHOLD}",
            "message": f"False-ready check failed: {exc}",
        }


def check_stripe_webhook_delivery() -> Dict[str, Any]:
    """Healthy if the last STRIPE_WEBHOOK_WINDOW Stripe events processed have
    failure rate <= STRIPE_WEBHOOK_FAILURE_RATE.

    Reads `paywall_stripe_event` (the idempotency tombstone shared by the X1
    paywall and the X4 consultant booking webhooks). A row with handled=False
    AND handler_error set is a delivery failure FIESTA saw — Stripe accepted
    the signature but our handler crashed / returned an error.

    Quiet windows: if fewer than STRIPE_WEBHOOK_MIN_EVENTS rows exist in the
    window, returns healthy with value=None and message='quiet'. This avoids
    paging on a single early failure when traffic is low.

    Origin: v1.0 plan + Gemini R1 Q6.2. Closes the previously-unmonitored
    blind spot where Stripe webhooks could be dropping without anyone
    noticing until paid customers complained.
    """
    try:
        from fiesta.paywall.models import StripeEvent, register_models
        register_models()
        if StripeEvent is None:
            return {
                "healthy": True,
                "value": None,
                "threshold": f"<= {STRIPE_WEBHOOK_FAILURE_RATE:.0%}",
                "message": "Stripe webhook check skipped — paywall models not registered.",
            }
        from app import db
        from sqlalchemy import text as _sql_text

        # Last N events (any handled state). Most-recent first.
        rows = (
            StripeEvent.query
            .order_by(StripeEvent.received_at.desc())
            .limit(STRIPE_WEBHOOK_WINDOW)
            .all()
        )
        total = len(rows)
        if total < STRIPE_WEBHOOK_MIN_EVENTS:
            return {
                "healthy": True,
                "value": total,
                "threshold": f"<= {STRIPE_WEBHOOK_FAILURE_RATE:.0%} once >={STRIPE_WEBHOOK_MIN_EVENTS} events",
                "message": f"Stripe webhook quiet: {total} events in window (min {STRIPE_WEBHOOK_MIN_EVENTS} to evaluate).",
            }
        failed = sum(1 for r in rows if not bool(r.handled) and r.handler_error)
        rate = failed / total
        healthy = rate <= STRIPE_WEBHOOK_FAILURE_RATE
        return {
            "healthy": healthy,
            "value": f"{failed}/{total} failed ({rate:.0%})",
            "threshold": f"<= {STRIPE_WEBHOOK_FAILURE_RATE:.0%}",
            "message": (
                f"Stripe webhook delivery ok — {failed}/{total} events failed ({rate:.0%})"
                if healthy
                else f"Stripe webhook FAILURE STREAK: {failed}/{total} events in window failed "
                     f"({rate:.0%}) — exceeded {STRIPE_WEBHOOK_FAILURE_RATE:.0%} threshold. "
                     f"Check /webhooks/stripe/paywall + /webhooks/stripe/consultant handler logs."
            ),
        }
    except Exception as exc:
        return {
            "healthy": False,
            "value": None,
            "threshold": f"<= {STRIPE_WEBHOOK_FAILURE_RATE:.0%}",
            "message": f"Stripe webhook delivery check failed: {exc}",
        }


# --------------------------------------------------------------------------- #
# HEALTH_CHECKS registry
# --------------------------------------------------------------------------- #
#
# Module-level mapping check_name → callable. Dispatcher iterates this in
# alphabetic order (stable output ordering for the JSON endpoint + alert text).
# Adding a new check = define the function above + add an entry here.
# --------------------------------------------------------------------------- #

HEALTH_CHECKS: Dict[str, Callable[[], Dict[str, Any]]] = {
    "cbsl_scraper_fresh":        check_cbsl_scraper_fresh,
    "fly_healthz":               check_fly_healthz,
    "neon_connection":           check_neon_connection,
    "celery_queue_depth":        check_celery_queue_depth,
    "gemini_cost_24h":           check_gemini_cost_24h,
    "false_ready_spike":         check_false_ready_spike,
    "stripe_webhook_delivery":   check_stripe_webhook_delivery,
}


# --------------------------------------------------------------------------- #
# run_all_checks — runs every check, wraps in try/except, returns snapshot
# --------------------------------------------------------------------------- #

def run_all_checks() -> Dict[str, Any]:
    """Run every check in HEALTH_CHECKS. Returns:

        {
          "ran_at": "<iso-8601>",
          "overall_healthy": bool,
          "unhealthy_count": int,
          "checks": {
              "<name>": {"healthy": bool, "value": ..., "threshold": ..., "message": ...},
              ...
          }
        }

    Each check is wrapped in a belt-and-braces try/except so a single broken
    check (e.g. someone refactored a column out from under it) doesn't kill
    the whole run.
    """
    ran_at = datetime.utcnow()
    results: Dict[str, Dict[str, Any]] = {}
    for name in sorted(HEALTH_CHECKS.keys()):
        fn = HEALTH_CHECKS[name]
        try:
            result = fn()
            # Defensive: ensure the contract shape even if a check returns
            # something weird.
            if not isinstance(result, dict) or "healthy" not in result:
                result = {
                    "healthy": False,
                    "value": None,
                    "threshold": None,
                    "message": f"check returned malformed result: {result!r}",
                }
        except Exception as exc:
            result = {
                "healthy": False,
                "value": None,
                "threshold": None,
                "message": f"check raised: {exc}",
            }
        results[name] = result

    unhealthy_count = sum(1 for r in results.values() if not r.get("healthy"))
    return {
        "ran_at": ran_at.isoformat(),
        "overall_healthy": unhealthy_count == 0,
        "unhealthy_count": unhealthy_count,
        "checks": results,
    }


# --------------------------------------------------------------------------- #
# dispatch_alert — write an Event + log; SendGrid stub for the future.
# --------------------------------------------------------------------------- #

def _sendgrid_alert(check_name: str, check_result: Dict[str, Any]) -> bool:
    """Send a SendGrid email to ops@smarter.tax. Returns True on success.

    Gated behind ENABLE_SENDGRID_ALERTS=true env var so this can be enabled
    only when the rate-limit + dedup story lands. Without dedup, a stuck
    failure would page ops every 5 minutes.

    Reuses the existing SendGrid wiring in app.py (SENDGRID_API_KEY env +
    sendgrid SDK). Body is intentionally terse — the dashboard at
    /internal/ops/health is the source of truth, alert is just a poke.
    """
    import os
    if os.environ.get("ENABLE_SENDGRID_ALERTS", "").lower() != "true":
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        api_key = os.environ.get("SENDGRID_API_KEY")
        if not api_key:
            log.warning("SENDGRID_API_KEY not set — skipping email alert")
            return False
        msg_body = (
            f"FIESTA Ops Sentinel ALERT\n\n"
            f"Check: {check_name}\n"
            f"Value: {check_result.get('value')}\n"
            f"Threshold: {check_result.get('threshold')}\n"
            f"Message: {check_result.get('message')}\n\n"
            f"Dashboard: /internal/ops/health\n"
        )
        mail = Mail(
            from_email="ops@smarter.tax",
            to_emails="ops@smarter.tax",
            subject=f"[FIESTA] Ops alert: {check_name}",
            plain_text_content=msg_body,
        )
        sg = SendGridAPIClient(api_key)
        sg.send(mail)
        return True
    except Exception as exc:
        log.warning("SendGrid alert dispatch failed for %s: %s", check_name, exc)
        return False


def dispatch_alert(check_name: str, check_result: Dict[str, Any]) -> Optional[int]:
    """Dispatch an alert for an unhealthy check.

    Today: writes an Event(event_type='ops_alert') row + logs a warning.
    Tomorrow (when ENABLE_SENDGRID_ALERTS=true): also sends a SendGrid email
    to ops@smarter.tax via the existing wiring in app.py.

    Returns the new Event.id on success, None on failure. NEVER raises.
    Telegram is deliberately not wired here — FIESTA has no Telegram surface
    (council #2 explicit). Telegram is a CEO-OS plane, not a FIESTA plane.
    """
    log.warning(
        "ops_sentinel ALERT %s: value=%r threshold=%r — %s",
        check_name,
        check_result.get("value"),
        check_result.get("threshold"),
        check_result.get("message"),
    )

    # SendGrid is gated behind an env flag — see _sendgrid_alert docstring.
    _sendgrid_alert(check_name, check_result)

    # Emit an Event row so the alert is visible in the Event Spine + can be
    # consumed by Wave 2 dashboards. We use the ad-hoc event_type 'ops_alert'
    # rather than expanding STANDARD_EVENTS (the orchestrator forbade
    # editing STANDARD_EVENTS in this wave). The Event Spine docstring
    # explicitly permits ad-hoc strings; we'll promote 'ops_alert' to
    # STANDARD_EVENTS in a follow-on PR.
    try:
        from events import emit
        return emit(
            event_type="ops_alert",
            payload={
                "check_name": check_name,
                "healthy": False,
                "value": str(check_result.get("value")),
                "threshold": str(check_result.get("threshold")),
                "message": check_result.get("message"),
            },
            source="cron:ops_sentinel",
        )
    except Exception as exc:
        log.warning("dispatch_alert: emit failed for %s: %s", check_name, exc)
        return None


# --------------------------------------------------------------------------- #
# Celery beat task
# --------------------------------------------------------------------------- #
#
# Registered under task name 'ops_sentinel.run_and_alert'. The beat schedule
# entry the orchestrator wires into celery_config.py:
#
#     'ops_sentinel-every-5min': {
#         'task': 'ops_sentinel.run_and_alert',
#         'schedule': crontab(minute='*/5'),
#     },
# --------------------------------------------------------------------------- #

# We import celery_app lazily so this module can still be imported (for
# log_gemini_cost / run_all_checks unit tests) in environments without celery
# fully wired (e.g. a partial test runner). The decorator is only applied
# when celery is importable.
try:
    from celery_config import app as celery_app
except Exception:  # pragma: no cover - celery is always present in real deploy
    celery_app = None  # type: ignore


if celery_app is not None:

    @celery_app.task(name="ops_sentinel.run_and_alert", ignore_result=True)
    def run_and_alert():
        """Celery beat entry point. Runs every 5 minutes (per the schedule the
        orchestrator wires into celery_config.app.conf.beat_schedule).

        Steps:
          1. run_all_checks()
          2. dispatch_alert() for every unhealthy result
          3. emit one summary 'ops_check_completed' event
        """
        snapshot = run_all_checks()
        for name, result in snapshot["checks"].items():
            if not result.get("healthy"):
                dispatch_alert(name, result)

        try:
            from events import emit
            emit(
                event_type="ops_check_completed",
                payload={
                    "ran_at": snapshot["ran_at"],
                    "overall_healthy": snapshot["overall_healthy"],
                    "unhealthy_count": snapshot["unhealthy_count"],
                    "checks": {
                        name: {"healthy": r.get("healthy"), "value": str(r.get("value"))}
                        for name, r in snapshot["checks"].items()
                    },
                },
                source="cron:ops_sentinel",
            )
        except Exception as exc:
            log.warning("ops_sentinel: emit ops_check_completed failed: %s", exc)

        return snapshot

else:  # pragma: no cover

    def run_and_alert():
        """Fallback when celery isn't importable — runs the same logic so the
        function is callable from CLI / tests."""
        snapshot = run_all_checks()
        for name, result in snapshot["checks"].items():
            if not result.get("healthy"):
                dispatch_alert(name, result)
        return snapshot


__all__ = [
    "HEALTH_CHECKS",
    "check_cbsl_scraper_fresh",
    "check_fly_healthz",
    "check_neon_connection",
    "check_celery_queue_depth",
    "check_gemini_cost_24h",
    "check_false_ready_spike",
    "run_all_checks",
    "dispatch_alert",
    "log_gemini_cost",
    "run_and_alert",
]
