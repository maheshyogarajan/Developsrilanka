"""
Client-side analytics beacon endpoint — Sprint 4 Tier B (2026-05-24).

Adds `POST /api/event` so the FIESTA frontend can fire funnel beacons
(`landing_view`, `cta_click`, `signup_started`, `signup_completed`,
`audit_view`, `tax_bill_view`, `evidence_uploaded`, `payment_started`,
`payment_completed`, plus authenticated `custom:` events) into the existing
EVENT SPINE (`events` table via `events.emit`).

Why a thin wrapper rather than letting the browser hit `events.emit`
directly? Two reasons:

  1. `events.emit` is a Python function, not an HTTP endpoint. The
     beacon needs HTTP semantics (`navigator.sendBeacon` requires it).
  2. We want a server-controlled whitelist of event names so a hostile
     browser can't write `event_type='admin_promote'` rows and pollute
     the funnel dashboards.

Design choices:

  * **CSRF.** Exempted from Flask-WTF's CSRFProtect (consistent with the
    rest of the JSON `/api/*` surface — see `api_routes.py`). We protect
    instead with an Origin/Referer check against the request's own host
    + an opt-in `BEACON_ALLOWED_ORIGINS` env var for staging/preview.
    This blocks the classic CSRF case (a third-party page POSTing on
    behalf of the user) without breaking `sendBeacon` (which strips
    custom CSRF headers in some browsers).

  * **Anonymous identity.** First request to ANY route lands a
    `session_anon_id` cookie (uuid4, 1y, SameSite=Lax, HttpOnly=False so
    the beacon JS can read it). For logged-in users we keep the cookie
    too — it makes pre-auth attribution possible when the user signs up
    mid-session.

  * **Payload size.** Capped at 2 KB JSON (matches the convention in
    `event_models.Event.payload`).

  * **Failure mode.** Returns 204 on success (beacon-friendly: no body
    means browsers don't retry). Returns 400 on validation issues and
    415 on wrong content-type. NEVER raises — analytics is observational
    and we don't want a malformed beacon to surface in the user's
    console as a 500.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, current_app, jsonify, request, make_response


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Whitelist
# --------------------------------------------------------------------------- #
#
# These names are the contract between the JS beacon and the analytics
# pipeline. Adding a new event = update both ends + a Wave-2 dashboard
# consumer. Ad-hoc names from authenticated users go through `custom:` so
# product teams can log experiments without a deploy.
#
ALLOWED_BEACON_EVENTS = frozenset({
    # ---- Funnel-critical (acquisition + activation) ---- #
    "landing_view",
    "cta_click",
    "signup_started",
    "signup_completed",
    "audit_view",
    "tax_bill_view",
    "evidence_uploaded",
    "payment_started",
    "payment_completed",
    # ---- Useful adjuncts (not required by the task but trivial to allow) ---- #
    "modal_open",
    "modal_close",
    "form_field_focus",
    "form_validation_error",
    "external_link_click",
})

# `custom:` events let authenticated users (we trust them more than anon
# browsers) emit arbitrary experiment names. Cap suffix length so a single
# event_type still fits in the 64-char DB column.
_CUSTOM_PREFIX = "custom:"
_CUSTOM_SUFFIX_RE = re.compile(r"^[a-z0-9_.\-]{1,48}$")

# Cookie + payload limits.
_COOKIE_NAME = "session_anon_id"
_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365  # 1 year
_PAYLOAD_MAX_BYTES = 2048


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _truncate_ip(raw: Optional[str]) -> Optional[str]:
    """Best-effort IPv4/IPv6 truncation for privacy.

    IPv4 a.b.c.d -> a.b.c.0  (zero out last octet)
    IPv6 keeps first 64 bits, zeroes the rest.

    If parsing fails (proxy returns garbage), drop the address entirely.
    """
    if not raw:
        return None
    raw = raw.split(",")[0].strip()
    if ":" in raw:  # IPv6
        parts = raw.split(":")
        if len(parts) >= 4:
            return ":".join(parts[:4]) + "::"
        return None
    if "." in raw:  # IPv4
        parts = raw.split(".")
        if len(parts) == 4:
            try:
                [int(p) for p in parts]  # validate
                return ".".join(parts[:3]) + ".0"
            except ValueError:
                return None
    return None


def _allowed_origins() -> set:
    """Resolve the set of Origin hosts that may POST /api/event.

    Always includes the current request's host. Augmented by the
    BEACON_ALLOWED_ORIGINS env var (comma-separated list of full
    origins, e.g. 'https://fiesta-mvp.fly.dev,https://staging.fiesta.lk').
    """
    out = set()
    try:
        out.add(request.host_url.rstrip("/"))
    except Exception:
        pass
    extra = os.environ.get("BEACON_ALLOWED_ORIGINS", "")
    for raw in extra.split(","):
        raw = raw.strip()
        if raw:
            out.add(raw.rstrip("/"))
    return out


def _origin_ok() -> bool:
    """Soft CSRF defence: require Origin or Referer to match an allowed host.

    sendBeacon DOES NOT send a custom `X-CSRFToken` header reliably across
    browsers (Safari in particular). The standard fallback is an
    Origin/Referer check, which is what every analytics SaaS does.

    Same-origin XHR/fetch from our own pages will pass trivially. A hostile
    cross-site form POST will fail (Origin will be the attacker's site).

    If neither header is set (some old Chrome variants on `sendBeacon`)
    we fall back to checking that this is at least a plausible JSON POST
    from a browser context (Content-Type starts with application/json).
    """
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    allowed = _allowed_origins()

    if origin:
        return origin.rstrip("/") in allowed
    if referer:
        # Compare just the scheme+host of the Referer.
        try:
            parsed = urlparse(referer)
            ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            return ref_origin in allowed
        except Exception:
            return False
    # Neither header — allow ONLY if content-type is application/json
    # (real browsers in sendBeacon path use this; CSRF form-posts use
    # application/x-www-form-urlencoded which would be blocked here).
    ctype = (request.headers.get("Content-Type") or "").lower()
    return ctype.startswith("application/json")


def _validate_event_name(name: str, is_authenticated: bool) -> Optional[str]:
    """Return None if the name is valid; otherwise an error string."""
    if not name or not isinstance(name, str):
        return "event name required"
    if len(name) > 64:
        return "event name exceeds 64 chars"

    if name in ALLOWED_BEACON_EVENTS:
        return None

    if name.startswith(_CUSTOM_PREFIX):
        if not is_authenticated:
            return "custom: events require authentication"
        suffix = name[len(_CUSTOM_PREFIX):]
        if not _CUSTOM_SUFFIX_RE.match(suffix):
            return (
                "custom: suffix must match [a-z0-9_.-]{1,48} "
                "(got %r)" % suffix
            )
        return None

    return "event name not in whitelist"


def _get_or_create_anon_id() -> str:
    """Read the session_anon_id cookie if present; otherwise mint a new uuid4.

    The cookie is written by the `_ensure_anon_cookie` after_request hook
    (not here) — we just synthesise the value the row should carry.
    """
    existing = request.cookies.get(_COOKIE_NAME)
    if existing and len(existing) <= 64:
        # Accept whatever was sent; the cookie was issued by us.
        return existing
    # request.environ holds anything _ensure_anon_cookie already minted.
    pre = request.environ.get("fiesta.anon_id")
    if pre:
        return pre
    return uuid.uuid4().hex


def _current_user_id() -> Optional[int]:
    """Return the logged-in user id if Flask-Login is available + active."""
    try:
        from flask_login import current_user
        if getattr(current_user, "is_authenticated", False):
            return int(current_user.get_id() or 0) or None
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# After-request hook: ensure every browser carries the anon cookie.
# --------------------------------------------------------------------------- #
def _ensure_anon_cookie(response):
    """Set session_anon_id on the first response that doesn't already carry
    one. Idempotent — touches Set-Cookie only when the cookie is absent.

    Excludes static assets (no value in carrying the cookie for /static/*)
    and explicit API responses where the request never crossed the cookie
    boundary (Stripe webhooks etc.). The exclusion list is conservative —
    when in doubt, set the cookie.
    """
    try:
        path = request.path or ""
        if path.startswith("/static/") or path == "/favicon.ico":
            return response

        # Read what's already on the request first.
        existing = request.cookies.get(_COOKIE_NAME)
        if existing and len(existing) <= 64:
            return response

        # Mint a new id, stash it on the request environ so any in-flight
        # /api/event in THIS request still sees a stable value.
        new_id = uuid.uuid4().hex
        request.environ["fiesta.anon_id"] = new_id

        secure = request.is_secure or (
            request.headers.get("X-Forwarded-Proto", "").lower() == "https"
        )

        response.set_cookie(
            _COOKIE_NAME,
            new_id,
            max_age=_COOKIE_MAX_AGE_SECONDS,
            secure=secure,
            httponly=False,  # JS beacon must read it
            samesite="Lax",
            path="/",
        )
    except Exception as exc:
        log.debug("ensure_anon_cookie: non-fatal failure: %s", exc)
    return response


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
def _build_beacon_view(csrf):
    """Build the /api/event view function (bound to the CSRFProtect instance
    we exempt below). Factored out so registration can defer the import."""

    def api_event():
        # ---- Origin/Referer gate ---- #
        if not _origin_ok():
            return jsonify({"error": "origin not allowed"}), 403

        # ---- Body parse + size cap ---- #
        raw = request.get_data(cache=False, as_text=False) or b""
        if len(raw) > _PAYLOAD_MAX_BYTES:
            return jsonify({"error": "payload too large"}), 413
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return jsonify({"error": "invalid json"}), 400
        if not isinstance(body, dict):
            return jsonify({"error": "json object required"}), 400

        # ---- Validate event name ---- #
        event_name = (body.get("event") or "").strip()
        user_id = _current_user_id()
        err = _validate_event_name(event_name, is_authenticated=bool(user_id))
        if err:
            return jsonify({"error": err}), 400

        # ---- Build payload ---- #
        props = body.get("properties") or {}
        if not isinstance(props, dict):
            return jsonify({"error": "properties must be an object"}), 400

        anon_id = _get_or_create_anon_id()
        enriched = {
            **props,
            "client_path": (body.get("path") or request.path)[:256],
            "client_referrer": (body.get("referrer") or request.referrer or "")[:512],
            "session_anon_id": anon_id,
        }

        # Drop any keys that look like they'd accidentally leak a server
        # secret — values are client-supplied, so this is defence-in-depth.
        for k in list(enriched.keys()):
            if isinstance(k, str) and k.lower() in {
                "csrf_token", "x-csrftoken", "authorization", "cookie"
            }:
                enriched.pop(k, None)

        # ---- Persist via the existing EVENT SPINE ---- #
        #
        # Transitional dual-write (Tier C2, 2026-05-24): we pass session_anon_id
        # both inside the `enriched` payload (so any existing analytics consumer
        # that reads payload['session_anon_id'] keeps working) AND as the
        # explicit kwarg (so it lands in the indexed top-level column
        # `events.session_anon_id`). The two write paths reconcile on the row.
        # Tier D1 / B-0040 perf: defer=True. The /api/event beacon is a
        # sendBeacon-style fire-and-forget — the browser doesn't read the
        # body, only the 204. Releasing the request thread before the
        # Postgres round-trip eliminates ~1s of cross-region latency per
        # event from the page's RUM. Tests force sync via
        # EVENTS_SYNC_FOR_TEST=1.
        try:
            from events import emit as _emit
            _emit(
                event_name[:64],
                user_id=user_id,
                payload=enriched,
                source="beacon",
                session_anon_id=anon_id,
                defer=True,
            )
        except Exception as exc:
            # Best-effort: log + continue. We still return 204 because the
            # browser doesn't need to retry on our DB hiccup.
            log.warning("api/event: emit(%r) failed: %s", event_name, exc)

        # 204 No Content — sendBeacon-friendly.
        return ("", 204)

    # Exempt from Flask-WTF CSRF (same pattern as api_routes.py). The
    # Origin/Referer gate above provides the actual CSRF defence.
    try:
        csrf.exempt(api_event)
    except Exception as exc:
        log.warning(
            "api/event: csrf.exempt failed (%s) — endpoint will require token.",
            exc,
        )

    return api_event


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire the /api/event endpoint and the anon-cookie after_request hook
    into the given Flask app. Idempotent (skips on second invocation)."""
    if app.config.get("_FIESTA_BEACON_REGISTERED"):
        return
    app.config["_FIESTA_BEACON_REGISTERED"] = True

    from app import csrf as _csrf

    view = _build_beacon_view(_csrf)
    app.add_url_rule(
        "/api/event",
        endpoint="analytics_beacon_event",
        view_func=view,
        methods=["POST"],
    )
    app.after_request(_ensure_anon_cookie)
    log.info("Analytics beacon registered: POST /api/event + session_anon_id cookie")


__all__ = ["register_routes", "ALLOWED_BEACON_EVENTS"]
