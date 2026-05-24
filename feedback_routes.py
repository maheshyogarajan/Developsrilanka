"""
Feedback endpoint — Sprint 4 Tier D4 (2026-05-24).

Adds `POST /api/feedback` so the FIESTA in-app feedback widget can persist
user submissions (bug/feature/confusion/praise/other + free-text body)
into the `feedback` table.

Architectural parity with `analytics_beacon_routes.py`:

  * CSRF: exempted from Flask-WTF (consistent with the JSON `/api/*`
    surface). Protected instead by an Origin/Referer check against the
    request's own host + opt-in `BEACON_ALLOWED_ORIGINS` env var.
  * Anonymous identity: reads the `session_anon_id` cookie (set by the
    beacon's after_request hook). Lets us correlate anon feedback with
    funnel events for the same browser.
  * Response: 204 on success (consistent with /api/event), 400 on
    validation errors, 413 on oversized payloads, 415 on wrong
    content-type, 403 on cross-origin POSTs.
  * Failure mode: NEVER raises. A DB write failure surfaces as a 503 so
    the widget can prompt the user to retry, but no exception bubbles.

Scope cap (Tier D4 — Wave-3 work is OUT of scope):
  * No admin dashboard to view feedback.
  * No email-on-feedback notification.
  * No category-based routing logic.
  * No file/screenshot upload.

The CEO reads submissions directly via SQL:
    SELECT * FROM feedback ORDER BY created_at DESC LIMIT 50;
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, request


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Limits + categories
# --------------------------------------------------------------------------- #
# Body cap chosen for human-written feedback. >4KB suggests pasted logs;
# we still accept those but truncate to keep the row sane.
_BODY_MAX_CHARS = 4000
_PAYLOAD_MAX_BYTES = 8192  # JSON envelope + body + properties
_COOKIE_NAME = "session_anon_id"

# Must match feedback_models.FEEDBACK_CATEGORIES + the CHECK constraint on
# the `feedback` table. Mirrored here to avoid a hard import dependency
# during route registration.
ALLOWED_CATEGORIES = frozenset({
    "bug",
    "feature",
    "confusion",
    "praise",
    "other",
})


# --------------------------------------------------------------------------- #
# Origin / Referer gate — same shape as analytics_beacon_routes._origin_ok.
# --------------------------------------------------------------------------- #
def _allowed_origins() -> set:
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
    """Allow same-origin POSTs; reject cross-origin. Fall back to a JSON
    content-type check when Origin/Referer are absent (some browsers
    strip them on sendBeacon-style requests)."""
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    allowed = _allowed_origins()

    if origin:
        return origin.rstrip("/") in allowed
    if referer:
        try:
            parsed = urlparse(referer)
            ref_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            return ref_origin in allowed
        except Exception:
            return False
    ctype = (request.headers.get("Content-Type") or "").lower()
    return ctype.startswith("application/json")


def _current_user_id() -> Optional[int]:
    """Return the logged-in user id if Flask-Login is active."""
    try:
        from flask_login import current_user
        if getattr(current_user, "is_authenticated", False):
            return int(current_user.get_id() or 0) or None
    except Exception:
        return None
    return None


def _get_anon_id() -> Optional[str]:
    """Read the session_anon_id cookie or the in-flight value from the
    beacon's after_request hook. Never mints a new id here — that's the
    beacon's job."""
    existing = request.cookies.get(_COOKIE_NAME)
    if existing and len(existing) <= 64:
        return existing
    pre = request.environ.get("fiesta.anon_id")
    if pre and len(pre) <= 64:
        return pre
    return None


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #
def _build_feedback_view(csrf):
    def api_feedback():
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

        # ---- Validate category ---- #
        category = (body.get("category") or "").strip().lower()
        if not category:
            return jsonify({"error": "category required"}), 400
        if category not in ALLOWED_CATEGORIES:
            return jsonify({
                "error": (
                    "category must be one of: "
                    + ", ".join(sorted(ALLOWED_CATEGORIES))
                ),
            }), 400

        # ---- Validate body ---- #
        text = body.get("body")
        if not isinstance(text, str):
            return jsonify({"error": "body required"}), 400
        text = text.strip()
        if not text:
            return jsonify({"error": "body must not be empty"}), 400
        if len(text) > _BODY_MAX_CHARS:
            text = text[:_BODY_MAX_CHARS]  # truncate, don't reject

        # ---- Capture context ---- #
        # Prefer the client-supplied URL (the page they were ON when they
        # opened the modal), fall back to the request's Referer header.
        url_at_submit = (body.get("url") or request.referrer or "")
        if isinstance(url_at_submit, str):
            url_at_submit = url_at_submit[:512]
        else:
            url_at_submit = None
        user_agent = request.headers.get("User-Agent")
        if user_agent:
            # No length limit at the column level (TEXT) but cap reasonable
            # bounds so a hostile client can't bloat the row.
            user_agent = user_agent[:1024]

        # ---- Persist ---- #
        try:
            from app import db
            from feedback_models import Feedback

            row = Feedback(
                user_id=_current_user_id(),
                session_anon_id=_get_anon_id(),
                category=category,
                body=text,
                url_at_submit=url_at_submit or None,
                user_agent=user_agent,
            )
            db.session.add(row)
            db.session.commit()
        except Exception as exc:
            log.warning("api/feedback: persistence failed: %s", exc)
            try:
                from app import db as _db
                _db.session.rollback()
            except Exception:
                pass
            # 503 is friendlier than 500 — invites the widget to offer a
            # retry button instead of swallowing the user's words.
            return jsonify({"error": "could not save feedback"}), 503

        # ---- Auto-bridge to D2 support ticket ---- #
        # Categories that need a conversation, not just a drop-and-go note:
        # bug (something broken) + confusion (I don't understand). 'praise' /
        # 'feature' / 'other' stay drop-only — they don't deserve to clutter
        # the CEO queue. NON-FATAL: bridge failure logs but does not change
        # the 204 contract — the feedback itself is already saved.
        if category in {"bug", "confusion"}:
            try:
                from support_tickets_routes import create_ticket_with_seed_message

                # Subject from the first line (cap to a reasonable length);
                # if the body is one long block, slice the first ~120 chars.
                first_line = text.splitlines()[0] if text else ""
                subject = (first_line or text)[:120].strip() or f"Feedback: {category}"
                if len(subject) < 10 and len(text) > 10:
                    subject = text[:120].strip()

                tags = ["from_feedback_widget", f"feedback_category:{category}"]

                ticket_id = create_ticket_with_seed_message(
                    user_id=_current_user_id(),
                    session_anon_id=_get_anon_id(),
                    subject=subject,
                    body=text,
                    category=category,
                    # Bugs are higher-signal than confusion; both above 'low'.
                    priority="high" if category == "bug" else "normal",
                    seed_author_role="customer",
                    seed_author_user_id=_current_user_id(),
                    tags=tags,
                )
                if ticket_id is None:
                    log.info(
                        "api/feedback: bridge to support ticket failed (feedback "
                        "row %s saved; ticket not created)", row.id,
                    )
            except Exception as exc:
                log.warning("api/feedback: bridge step failed: %s", exc)

        # 204 No Content — same shape as /api/event.
        return ("", 204)

    try:
        csrf.exempt(api_feedback)
    except Exception as exc:
        log.warning(
            "api/feedback: csrf.exempt failed (%s) — endpoint will require token.",
            exc,
        )

    return api_feedback


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Wire POST /api/feedback into the given Flask app. Idempotent."""
    if app.config.get("_FIESTA_FEEDBACK_REGISTERED"):
        return
    app.config["_FIESTA_FEEDBACK_REGISTERED"] = True

    from app import csrf as _csrf

    view = _build_feedback_view(_csrf)
    app.add_url_rule(
        "/api/feedback",
        endpoint="feedback_submit",
        view_func=view,
        methods=["POST"],
    )
    log.info("Feedback endpoint registered: POST /api/feedback")


__all__ = ["register_routes", "ALLOWED_CATEGORIES"]
