"""
UTM capture + persistence — Tier D6 / A2 (2026-05-24).

Captures the five standard ``utm_*`` query-string parameters on EVERY
request that carries them, persists them to the Flask session for the
duration of the visit, and provides helpers to:

  1. Attach UTM parameters to event payloads (so every funnel emit gets
     attribution baked in).
  2. Lift UTMs from session onto the User row at signup time.
  3. Expose first-touch attribution to templates as ``utm_first_touch``.

DESIGN PRINCIPLES
=================

1. **First-touch wins.** Once a UTM tuple is set on the session, later
   visits with different UTMs DO NOT overwrite it. The first ad click
   that brought the user into FIESTA is the source of record. We do
   capture the most recent click separately as ``utm_last_touch`` for
   anti-attribution-fraud diagnostics.

2. **PII-clean.** Only the five standard UTM params are persisted. We
   never capture full query strings. Each value is capped at 128 chars
   and stripped of control characters.

3. **Never breaks the request.** Any exception in capture is swallowed
   and logged at debug. UTM is observational, not transactional.

4. **Backward-compatible with the existing single-channel capture.**
   ``lankatax_onboarding_routes`` already reads ``utm_source`` directly
   from the query string. That path keeps working; this module adds a
   broader funnel that catches Meta/LinkedIn/Twitter clicks too.

5. **No new DB writes per request.** Capture goes to the Flask session
   cookie (signed, server-side via Flask-Session). DB write only happens
   at the signup boundary (and only if the user doesn't already carry
   attribution).

PUBLIC API
==========

* ``register(app)``               — wires the before_request hook
* ``current_utm()``               — dict of currently-captured UTM params
* ``utm_for_payload()``           — UTM dict suitable for event payload
* ``persist_to_user(user)``       — lift session UTMs onto User row
* ``UTM_FIELDS``                  — frozenset of the 5 keys we track

UTM DATA FLOW
=============

::

    1. Ad click lands on https://fiesta-mvp.fly.dev/?utm_source=meta
       &utm_medium=cpc&utm_campaign=diaspora_q3
                              |
                              v
    2. utm_capture._before_request_capture()
         session['utm_first_touch'] = {
             'utm_source': 'meta',
             'utm_medium': 'cpc',
             'utm_campaign': 'diaspora_q3',
             'captured_at': '2026-05-24T...',
             'landing_path': '/',
         }
         session['utm_last_touch'] = {...same fields...}
                              |
                              v
    3. Every fiestaTrack('landing_view') beacon hits /api/event
       analytics_beacon_routes builds payload, INCLUDES utm_for_payload()
       (via the hook we register on the beacon endpoint).
                              |
                              v
    4. User signs up — fiesta/signup/routes.signup_submit
       calls utm_capture.persist_to_user(new_user) which writes
       utm_source / utm_medium / utm_campaign / utm_term / utm_content
       onto the User row (idempotent — never overwrites non-null values).
                              |
                              v
    5. Every authed event (paywall_checkout_started, etc.) still gets
       the same utm dict in payload via utm_for_payload(), so the
       conversion-side funnel queries can JOIN on either:
         events.payload->>'utm_source'    (per-event attribution)
         user.utm_source                  (lifetime attribution)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from flask import Flask, request, session


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# The five standard UTM params (Google Analytics convention)
# --------------------------------------------------------------------------- #
UTM_FIELDS = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
})

# Session keys
_SESS_FIRST_TOUCH = "utm_first_touch"
_SESS_LAST_TOUCH = "utm_last_touch"

# Per-value cap to prevent pathological clicks from inflating session cookies.
_MAX_VALUE_LEN = 128

# Strip control + non-printable. Allow standard URL-safe chars + spaces +
# common campaign separators. Anything stranger is dropped.
_SAFE_RE = re.compile(r"[^\w\s.\-+%~/:@(),\[\]]+")


def _sanitise(raw: Optional[str]) -> Optional[str]:
    """Trim, strip control chars, cap length. Returns None on empty."""
    if not raw or not isinstance(raw, str):
        return None
    cleaned = _SAFE_RE.sub("", raw).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_VALUE_LEN]


def _read_utms_from_query() -> dict:
    """Extract the five UTM params from request.args. Returns only the
    non-empty ones. Never raises."""
    out = {}
    try:
        for field in UTM_FIELDS:
            v = _sanitise(request.args.get(field))
            if v:
                out[field] = v
    except Exception as exc:
        log.debug("utm_capture: query-string read failed: %s", exc)
    return out


# --------------------------------------------------------------------------- #
# before_request hook
# --------------------------------------------------------------------------- #
def _before_request_capture() -> None:
    """If the inbound request carries any UTM params, persist them to the
    session. First-touch is sticky; last-touch updates every visit.

    NEVER raises. Static-asset paths are skipped to keep the hook cheap.
    """
    try:
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return
        if request.method not in ("GET", "HEAD"):
            # POSTs to /signup, /api/event etc. should not be the surface
            # where we mint attribution — the GET that preceded them is.
            return

        utms = _read_utms_from_query()
        if not utms:
            return

        # Build the touch record. Capture metadata that helps debug
        # weird attribution later (e.g. when did this ad click happen?).
        touch = dict(utms)
        touch["captured_at"] = datetime.utcnow().isoformat() + "Z"
        touch["landing_path"] = path[:256]

        # First-touch: only set if missing. Once set, sticky for the
        # lifetime of the session cookie.
        if not session.get(_SESS_FIRST_TOUCH):
            session[_SESS_FIRST_TOUCH] = touch

        # Last-touch: always overwrite. Lets us answer "was their last
        # click before paying actually the source we attribute to?"
        session[_SESS_LAST_TOUCH] = touch
    except Exception as exc:
        log.debug("utm_capture: before_request failed: %s", exc)


# --------------------------------------------------------------------------- #
# Read helpers
# --------------------------------------------------------------------------- #
def current_utm(prefer: str = "first") -> dict:
    """Return the currently-active UTM dict (or empty dict).

    Args:
      prefer: 'first' (default) returns first-touch; 'last' returns
              the most-recent touch.

    Result is a flat dict with only the 5 UTM keys (no captured_at /
    landing_path metadata — those stay in the session).
    """
    try:
        key = _SESS_FIRST_TOUCH if prefer != "last" else _SESS_LAST_TOUCH
        touch = session.get(key) or {}
        return {f: touch[f] for f in UTM_FIELDS if f in touch}
    except Exception as exc:
        log.debug("utm_capture: current_utm() failed: %s", exc)
        return {}


def utm_for_payload() -> dict:
    """Return a dict suitable to merge into an event payload.

    Combines first-touch (primary attribution) with last-touch (for
    diagnostics). Keys are namespaced so they don't collide with
    application-specific payload fields.

    Shape::

        {
          'utm_source': 'meta',          # first-touch (primary)
          'utm_medium': 'cpc',
          'utm_campaign': 'diaspora_q3',
          'utm_first_touch_at': '...',   # ISO timestamp
          'utm_last_touch_source': 'linkedin',  # only when differs
        }

    Returns empty dict if no UTM context exists.
    """
    try:
        first = session.get(_SESS_FIRST_TOUCH) or {}
        last = session.get(_SESS_LAST_TOUCH) or {}
        if not first and not last:
            return {}

        out = {}
        # First-touch fields are the primary attribution.
        for f in UTM_FIELDS:
            if f in first:
                out[f] = first[f]
        if first.get("captured_at"):
            out["utm_first_touch_at"] = first["captured_at"]

        # Last-touch source only added when it materially differs from
        # first-touch — keeps the payload small in the typical case.
        first_src = first.get("utm_source")
        last_src = last.get("utm_source")
        if last_src and last_src != first_src:
            out["utm_last_touch_source"] = last_src
            if last.get("captured_at"):
                out["utm_last_touch_at"] = last["captured_at"]

        return out
    except Exception as exc:
        log.debug("utm_capture: utm_for_payload() failed: %s", exc)
        return {}


# --------------------------------------------------------------------------- #
# Write to User row at signup
# --------------------------------------------------------------------------- #
def persist_to_user(user) -> bool:
    """Lift first-touch UTMs from session onto the User row. Idempotent:
    never overwrites a non-null existing value (the user might have
    already been attributed via another flow, e.g. lankatax_onboarding).

    Returns True iff at least one column was written. Never raises;
    logs at debug and returns False on any error.

    Caller is responsible for committing the SQLAlchemy session.
    """
    if user is None:
        return False
    try:
        first = session.get(_SESS_FIRST_TOUCH) or {}
        if not first:
            return False

        wrote = False
        for f in UTM_FIELDS:
            v = first.get(f)
            if not v:
                continue
            # Only persist if the User has the column AND it's currently null.
            if not hasattr(user, f):
                continue
            if getattr(user, f, None):
                continue
            setattr(user, f, v[:128])
            wrote = True
        return wrote
    except Exception as exc:
        log.debug("utm_capture: persist_to_user() failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Flask wiring
# --------------------------------------------------------------------------- #
def register(app: Flask) -> None:
    """Install the before_request hook + context processor.

    Idempotent — registers at most once per app.
    """
    if app.config.get("_FIESTA_UTM_CAPTURE_REGISTERED"):
        return
    app.config["_FIESTA_UTM_CAPTURE_REGISTERED"] = True

    app.before_request(_before_request_capture)

    @app.context_processor
    def _inject_utm_first_touch():
        # Best-effort — empty dict outside a request context.
        try:
            return {"utm_first_touch": current_utm("first")}
        except Exception:
            return {"utm_first_touch": {}}

    log.info("UTM capture registered: before_request hook + utm_first_touch context")


__all__ = [
    "register",
    "current_utm",
    "utm_for_payload",
    "persist_to_user",
    "UTM_FIELDS",
]
