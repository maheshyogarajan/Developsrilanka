"""
fiesta.paywall.stripe_config — Stripe key-mode detection + startup validation.

Added 2026-05-20 as a pre-deploy blocker fix for FIESTA v1.

Purpose
-------
v1 ships with the X1 paywall (POST /webhooks/stripe/paywall, POST
/pricing/x1/checkout). At deploy time the CEO swaps the test-mode keys
for live-mode keys. Without a visible mode indicator there is no quick
way to confirm "yes, live keys are loaded" before flipping DNS to the
new box — the only way to know was to wait for the first real payment.

This module provides:

  detect_stripe_mode()              -> "live" | "test" | "missing"
  detect_webhook_mode()             -> "live" | "test" | "missing"
  validate_stripe_config(strict)    -> dict (mode, ready, issues, ...)
  log_startup_stripe_status(app)    -> log line at app boot for operator visibility

The contract is intentionally non-fatal in dev environments: if the
LIVE_* env vars are absent we WARN but do NOT crash the app. Production
deploys pass `STRIPE_LIVE_KEYS_REQUIRED=1` to make missing live keys an
error at boot (the gunicorn worker still starts so /healthz/stripe can
report the problem; the route handlers then 503 on first request).

Env var contract
----------------

  STRIPE_SECRET_KEY                  required at boot (test or live).
                                     Prefix `sk_test_` -> test mode.
                                     Prefix `sk_live_` -> live mode.
  STRIPE_PAYWALL_WEBHOOK_SECRET      X1 webhook signing secret. Falls
                                     back to STRIPE_WEBHOOK_SECRET if
                                     the X1-specific var is unset.
  STRIPE_LIVE_WEBHOOK_SECRET         Optional explicit live-mode webhook
                                     secret. If set AND mode == "live",
                                     it MUST equal the active webhook
                                     secret OR validation reports a
                                     mismatch issue.
  STRIPE_LIVE_KEYS_REQUIRED          "1" / "true" makes "live" mandatory
                                     at boot. In production deploys this
                                     should be set; in dev it's unset.

The /healthz/stripe route exposes the JSON shape of validate_stripe_config
so an operator (or external monitoring) can check the box BEFORE flipping
the deploy.

This module is import-safe with NO Flask app context required.
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Detection helpers.
# --------------------------------------------------------------------------- #

def _classify_stripe_secret(value: str | None) -> str:
    """Return 'live' / 'test' / 'missing' / 'unknown' for a stripe secret key."""
    if not value:
        return "missing"
    v = value.strip()
    if not v:
        return "missing"
    if v.startswith("sk_live_") or v.startswith("rk_live_"):
        return "live"
    if v.startswith("sk_test_") or v.startswith("rk_test_"):
        return "test"
    return "unknown"


def _classify_webhook_secret(value: str | None) -> str:
    """Return 'configured' / 'missing' for a Stripe webhook signing secret.

    Stripe webhook secrets are all `whsec_...` regardless of test/live —
    the mode is implicit (it must match the API key's mode). So we only
    expose 'configured' / 'missing' here. Live/test mismatch detection
    is layered into validate_stripe_config() via the explicit
    STRIPE_LIVE_WEBHOOK_SECRET env var (when present).
    """
    if not value:
        return "missing"
    v = value.strip()
    if not v:
        return "missing"
    if v.startswith("whsec_"):
        return "configured"
    # Could also be a raw secret in a CI environment; accept anyway.
    return "configured"


def detect_stripe_mode() -> str:
    """Detect the active Stripe mode from STRIPE_SECRET_KEY.

    Returns:
        "live"    -> sk_live_* key present
        "test"    -> sk_test_* key present
        "missing" -> env var unset / empty
        "unknown" -> env var set but does not match either prefix
    """
    return _classify_stripe_secret(os.environ.get("STRIPE_SECRET_KEY"))


def detect_webhook_mode() -> str:
    """Detect whether a Stripe webhook signing secret is configured.

    Reads STRIPE_PAYWALL_WEBHOOK_SECRET first, falls back to
    STRIPE_WEBHOOK_SECRET. Returns 'configured' / 'missing'.
    """
    secret = (
        os.environ.get("STRIPE_PAYWALL_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_WEBHOOK_SECRET")
    )
    return _classify_webhook_secret(secret)


# --------------------------------------------------------------------------- #
# Validation.
# --------------------------------------------------------------------------- #

def _is_truthy(env_value: str | None) -> bool:
    if not env_value:
        return False
    return env_value.strip().lower() in {"1", "true", "yes", "on"}


def validate_stripe_config(*, strict: bool | None = None) -> dict[str, Any]:
    """Return a JSON-serialisable health snapshot for Stripe configuration.

    Args:
        strict: when True, treat 'live' mode as REQUIRED and report
            missing live keys as issues. When None (default), read
            STRIPE_LIVE_KEYS_REQUIRED from env.

    Returns dict with keys:
        mode                 ("live" | "test" | "missing" | "unknown")
        webhook              ("configured" | "missing")
        ready                bool  -- True iff there are no blocking issues
        live_required        bool
        issues               list[str]
        warnings             list[str]
        live_webhook_match   bool | None  -- only set when both
                             STRIPE_LIVE_WEBHOOK_SECRET and the active
                             webhook secret are present
    """
    if strict is None:
        strict = _is_truthy(os.environ.get("STRIPE_LIVE_KEYS_REQUIRED"))

    mode = detect_stripe_mode()
    webhook = detect_webhook_mode()
    issues: list[str] = []
    warnings: list[str] = []

    if mode == "missing":
        msg = "STRIPE_SECRET_KEY not set"
        if strict:
            issues.append(msg)
        else:
            warnings.append(msg + " (dev mode tolerated)")
    elif mode == "unknown":
        # A non-standard value. Treat as an issue regardless of strict —
        # it means the deploy almost certainly has a typo.
        issues.append(
            "STRIPE_SECRET_KEY does not start with sk_test_ or sk_live_ "
            "(misconfigured)"
        )
    elif mode == "test" and strict:
        issues.append(
            "STRIPE_LIVE_KEYS_REQUIRED=1 but STRIPE_SECRET_KEY is a test key"
        )

    if webhook == "missing":
        msg = (
            "STRIPE_PAYWALL_WEBHOOK_SECRET (and fallback STRIPE_WEBHOOK_SECRET) "
            "not set"
        )
        if strict:
            issues.append(msg)
        else:
            warnings.append(msg + " (dev mode tolerated)")

    # Check explicit live-webhook secret matches the active one.
    live_webhook_match: bool | None = None
    live_wh = os.environ.get("STRIPE_LIVE_WEBHOOK_SECRET")
    active_wh = (
        os.environ.get("STRIPE_PAYWALL_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_WEBHOOK_SECRET")
    )
    if live_wh and active_wh:
        live_webhook_match = live_wh.strip() == active_wh.strip()
        if mode == "live" and not live_webhook_match:
            issues.append(
                "STRIPE_LIVE_WEBHOOK_SECRET set but does not equal the active "
                "webhook secret (mode=live; live-mode mismatch is a deploy bug)"
            )

    # `ready` means the paywall can actually serve a checkout + verify a
    # webhook. In dev/test environments the missing-key state is warned-only
    # (so the app still boots), but ready remains False — the checkout
    # route already 503s on missing keys, so this is consistent.
    ready = (
        len(issues) == 0
        and mode in {"test", "live"}
        and webhook == "configured"
    )

    return {
        "mode": mode,
        "webhook": webhook,
        "ready": ready,
        "live_required": strict,
        "issues": issues,
        "warnings": warnings,
        "live_webhook_match": live_webhook_match,
    }


# --------------------------------------------------------------------------- #
# Startup hook.
# --------------------------------------------------------------------------- #

def log_startup_stripe_status(app=None) -> dict[str, Any]:
    """Emit a single human-readable line about Stripe config at app boot.

    Designed to be called from register_routes() exactly once. Returns the
    validation dict so callers can log it elsewhere too.
    """
    snapshot = validate_stripe_config()
    mode = snapshot["mode"]
    webhook = snapshot["webhook"]
    ready = snapshot["ready"]
    issues = snapshot["issues"]
    warnings = snapshot["warnings"]

    if mode == "live" and ready:
        log.info(
            "Stripe paywall: mode=LIVE, webhook=%s, ready=YES "
            "(live keys validated at boot)",
            webhook,
        )
    elif mode == "test" and ready:
        log.info(
            "Stripe paywall: mode=TEST, webhook=%s, ready=YES "
            "(test keys -- swap to live before public launch)",
            webhook,
        )
    elif issues:
        log.error(
            "Stripe paywall: mode=%s, webhook=%s, ready=NO -- issues=%s",
            mode, webhook, issues,
        )
    else:
        log.warning(
            "Stripe paywall: mode=%s, webhook=%s, ready=NO -- warnings=%s",
            mode, webhook, warnings,
        )

    return snapshot


__all__ = [
    "detect_stripe_mode",
    "detect_webhook_mode",
    "validate_stripe_config",
    "log_startup_stripe_status",
]
