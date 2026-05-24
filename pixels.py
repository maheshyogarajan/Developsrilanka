"""
Paid-acquisition pixel configuration — Tier D6 / A2 (2026-05-24).

Centralises the rules for whether Meta / LinkedIn / Twitter pixels render
on a page. The actual pixel JS lives in ``templates/components/pixels.html``;
this module is the single source of truth for "should the pixel fire?".

DESIGN PRINCIPLES
=================

1. **Default-OFF.** ``PIXELS_ENABLED`` defaults to ``false``. Even after CEO
   sets real pixel IDs, the master kill switch must be flipped to true
   before any pixel JS reaches a browser. This makes deploy risk-free —
   we can ship the integration and let CEO turn it on after verifying
   the env vars in Fly secrets.

2. **Per-network env gates.** Each of ``META_PIXEL_ID``,
   ``LINKEDIN_PARTNER_ID``, ``TWITTER_PIXEL_ID`` independently gates its
   pixel. A network with no ID set is silently suppressed even when the
   master switch is on.

3. **Test-mode suppression.** When ``Flask.testing`` is true OR
   ``FLASK_ENV == 'test'`` OR ``PIXELS_DISABLE_IN_TEST=1``, pixels never
   render regardless of other config. Avoids polluting Meta/LinkedIn/X
   analytics with synthetic test traffic.

4. **Dev-mode suppression.** When ``FLASK_ENV == 'development'`` AND
   ``PIXELS_ALLOW_IN_DEV != '1'``, pixels are suppressed in local dev so
   developers don't accidentally fire ad-network conversions while
   working on a feature.

5. **No real IDs in code.** All four env vars are read at request time
   (not import time) so changing a Fly secret takes effect on the next
   request without a redeploy.

6. **Safe defaults.** All public helpers return safe falsey values on
   any exception — pixel config must NEVER raise into the request flow.

PUBLIC API
==========

* ``pixels_enabled() -> bool``  — master switch + env discipline
* ``pixel_config() -> dict``    — full per-network config for templates
* ``register(app)``             — wires the context processor

CONTEXT EXPOSED TO TEMPLATES
============================

Templates can reference::

    {% if pixel_cfg.enabled %}
      {% include 'components/pixels.html' %}
    {% endif %}

The ``pixel_cfg`` dict shape::

    {
      "enabled": bool,        # master gate (after all suppression rules)
      "meta_id": str | None,
      "linkedin_id": str | None,
      "twitter_id": str | None,
    }

A pixel only renders when ``enabled AND <network>_id is truthy``.

TESTS
=====

See ``tests/pixels/test_pixels.py``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from flask import Flask


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Env var names — single source of truth
# --------------------------------------------------------------------------- #
ENV_MASTER_SWITCH = "PIXELS_ENABLED"
ENV_META_PIXEL_ID = "META_PIXEL_ID"
ENV_LINKEDIN_PARTNER_ID = "LINKEDIN_PARTNER_ID"
ENV_TWITTER_PIXEL_ID = "TWITTER_PIXEL_ID"

# Test/dev suppression knobs
ENV_DISABLE_IN_TEST = "PIXELS_DISABLE_IN_TEST"   # default behaviour anyway
ENV_ALLOW_IN_DEV = "PIXELS_ALLOW_IN_DEV"         # explicit opt-in for dev

# Format regexes — soft validation, not enforcement.
# Meta Pixel IDs are numeric, typically 15-16 digits.
# LinkedIn Partner IDs are numeric, typically 6-7 digits.
# Twitter (X) Pixel IDs are alphanumeric, typically 5-6 chars (e.g. "abc12").


# --------------------------------------------------------------------------- #
# Truthy parsing
# --------------------------------------------------------------------------- #
_TRUTHY = frozenset({"1", "true", "yes", "on", "y", "t"})


def _is_truthy(raw: Optional[str]) -> bool:
    """Permissive truthy parser. Empty / None / unknown -> False."""
    if not raw:
        return False
    return str(raw).strip().lower() in _TRUTHY


# --------------------------------------------------------------------------- #
# Test / dev suppression
# --------------------------------------------------------------------------- #
def _in_test_mode() -> bool:
    """True when we're running under pytest or explicit test env."""
    # PYTEST_CURRENT_TEST is set by pytest for every test invocation.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    if _is_truthy(os.environ.get(ENV_DISABLE_IN_TEST)):
        return True
    flask_env = (os.environ.get("FLASK_ENV") or "").strip().lower()
    if flask_env == "test":
        return True
    # Flask 2.x deprecated FLASK_ENV in favour of FLASK_DEBUG + app.config.
    # We honour both — Flask's `app.testing` is checked in pixels_enabled().
    return False


def _in_dev_mode() -> bool:
    """True when we're running locally (FLASK_ENV=development) without
    explicit opt-in via PIXELS_ALLOW_IN_DEV=1."""
    flask_env = (os.environ.get("FLASK_ENV") or "").strip().lower()
    if flask_env not in {"development", "dev", "local"}:
        return False
    if _is_truthy(os.environ.get(ENV_ALLOW_IN_DEV)):
        return False
    return True


def _flask_app_testing() -> bool:
    """True when current_app.testing is set. Factored out so tests can
    monkeypatch this independently of the underlying Flask config."""
    try:
        from flask import current_app
        return bool(current_app and getattr(current_app, "testing", False))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def pixels_enabled() -> bool:
    """Return True iff the master kill switch is on AND no suppression
    rule fires.

    Order of evaluation (any False -> False):

      1. ``PIXELS_ENABLED`` env var is truthy.
      2. We are not in test mode (pytest / FLASK_ENV=test / opt-in flag).
      3. We are not in dev mode without ``PIXELS_ALLOW_IN_DEV=1``.
      4. Flask app.testing is not set (belt + braces with #2).

    Never raises. Returns False on any exception path.
    """
    try:
        if not _is_truthy(os.environ.get(ENV_MASTER_SWITCH)):
            return False
        if _in_test_mode():
            return False
        if _in_dev_mode():
            return False
        if _flask_app_testing():
            return False
        return True
    except Exception as exc:
        log.debug("pixels_enabled() failed (defaulting to False): %s", exc)
        return False


def _read_id(env_name: str) -> Optional[str]:
    """Read + sanity-check a pixel ID env var. Returns None for empty /
    suspicious values. Caps length at 64 to prevent any pathological env
    from leaking into rendered HTML."""
    raw = os.environ.get(env_name)
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Reject placeholder values that look like a developer forgot to fill in.
    placeholder_markers = {
        "your_pixel_id", "your-pixel-id", "changeme", "change-me",
        "todo", "placeholder", "xxxxx", "xxxxxxxxx",
    }
    if raw.lower() in placeholder_markers:
        return None
    if len(raw) > 64:
        log.warning("Pixel env %s value exceeds 64 chars; ignoring.", env_name)
        return None
    # Pixel IDs are alphanumeric with at most underscores/dashes. Strip
    # anything else to be defensive (template escaping handles this too,
    # but defence in depth is cheap).
    safe = "".join(c for c in raw if c.isalnum() or c in "_-")
    return safe or None


def pixel_config() -> dict:
    """Resolve the full pixel config for the current request.

    Returns a dict with:
      * ``enabled``      master gate (after all suppression)
      * ``meta_id``      Meta Pixel ID or None
      * ``linkedin_id``  LinkedIn Partner ID or None
      * ``twitter_id``   Twitter/X Pixel ID or None

    A network only renders when ``enabled AND <network>_id`` are both set.
    Never raises.
    """
    try:
        enabled = pixels_enabled()
        return {
            "enabled": enabled,
            "meta_id": _read_id(ENV_META_PIXEL_ID) if enabled else None,
            "linkedin_id": _read_id(ENV_LINKEDIN_PARTNER_ID) if enabled else None,
            "twitter_id": _read_id(ENV_TWITTER_PIXEL_ID) if enabled else None,
        }
    except Exception as exc:
        log.debug("pixel_config() failed (returning empty): %s", exc)
        return {
            "enabled": False,
            "meta_id": None,
            "linkedin_id": None,
            "twitter_id": None,
        }


# --------------------------------------------------------------------------- #
# Conversion-event surface flags
# --------------------------------------------------------------------------- #
#
# Templates set these via the `pixel_event` Jinja variable when they want
# a specific conversion to fire. The component pixels.html reads it.
#
# Recognised events:
#   "signup_started"             — user landed on signup form
#   "signup_completed"           — user finished signup
#   "paid_subscription_started"  — Stripe checkout init
#   "paid_subscription_completed" — Stripe checkout success
#
SUPPORTED_PIXEL_EVENTS = frozenset({
    "signup_started",
    "signup_completed",
    "paid_subscription_started",
    "paid_subscription_completed",
})


# --------------------------------------------------------------------------- #
# Flask wiring
# --------------------------------------------------------------------------- #
def register(app: Flask) -> None:
    """Register the ``pixel_cfg`` context processor on ``app``.

    Idempotent — registers at most once per app. After registration,
    every template render gets ``pixel_cfg`` in its context, e.g.::

        {% if pixel_cfg.enabled and pixel_cfg.meta_id %}
          <!-- Meta Pixel renders here -->
        {% endif %}

    Templates that want to fire a conversion event also set
    ``pixel_event`` in their local context (via the ``with`` keyword on
    include, or as a route-passed variable).
    """
    if app.config.get("_FIESTA_PIXELS_REGISTERED"):
        return
    app.config["_FIESTA_PIXELS_REGISTERED"] = True

    @app.context_processor
    def _inject_pixel_cfg():
        cfg = pixel_config()
        return {
            "pixel_cfg": cfg,
            # Convenience flags so templates can write `{% if pixel_meta %}`
            # without digging into the dict.
            "pixel_meta": bool(cfg.get("enabled") and cfg.get("meta_id")),
            "pixel_linkedin": bool(cfg.get("enabled") and cfg.get("linkedin_id")),
            "pixel_twitter": bool(cfg.get("enabled") and cfg.get("twitter_id")),
        }

    log.info(
        "Pixels registered: enabled=%s meta=%s linkedin=%s twitter=%s",
        pixels_enabled(),
        bool(_read_id(ENV_META_PIXEL_ID)),
        bool(_read_id(ENV_LINKEDIN_PARTNER_ID)),
        bool(_read_id(ENV_TWITTER_PIXEL_ID)),
    )


__all__ = [
    "pixels_enabled",
    "pixel_config",
    "register",
    "SUPPORTED_PIXEL_EVENTS",
    "ENV_MASTER_SWITCH",
    "ENV_META_PIXEL_ID",
    "ENV_LINKEDIN_PARTNER_ID",
    "ENV_TWITTER_PIXEL_ID",
]
