"""
In-app nudge API — Wave 3.1 (2026-05-18).

Three endpoints under /api/in_app_nudges/* that the layout.html JS polls to
render the dismissible banner shell:

    GET    /api/in_app_nudges                 → list undismissed banners (JSON)
    POST   /api/in_app_nudges/<id>/dismiss    → soft-delete (sets dismissed_at)
    POST   /api/in_app_nudges/<id>/click      → emit nudge_clicked + 302 to cta_url

All routes require @login_required. Authorisation check: the banner must
belong to current_user — we never let user A dismiss / click user B's banner.

Event emissions:
    GET /api/in_app_nudges     → emits `nudge_viewed` once per request that
                                 actually returns >= 1 banner. (No-banner GETs
                                 don't emit, to keep the event stream clean.)
    POST .../dismiss           → emits `nudge_dismissed`
    POST .../click             → emits `nudge_clicked`

The `nudge_clicked` event is the conversion signal the engagement engine
cohort analysis (Wave 3.2+) tunes against. Don't drop it.
"""
from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, redirect, abort, request
from flask_login import login_required, current_user

log = logging.getLogger(__name__)


in_app_nudge_bp = Blueprint(
    "in_app_nudges", __name__, url_prefix="/api/in_app_nudges"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _safe_emit(event_type: str, payload: dict) -> None:
    """Best-effort event emit; never raises (events.emit already swallows,
    we wrap again for paranoid defence)."""
    try:
        from events import emit
        emit(
            event_type=event_type,
            user_id=getattr(current_user, "id", None),
            payload=payload,
            source="route:in_app_nudges",
        )
    except Exception as e:
        log.warning("in_app_nudges: emit %r failed: %s", event_type, e)


def _load_banner_or_404(banner_id: int):
    """Fetch a banner row owned by current_user. abort(404) if missing OR
    owned by a different user (we don't leak existence across accounts)."""
    from engagement_models import InAppBanner
    row = InAppBanner.query.get(banner_id)
    if row is None:
        abort(404)
    if row.user_id != current_user.id:
        # Don't 403 — that confirms the banner exists. 404 keeps it opaque.
        abort(404)
    return row


# --------------------------------------------------------------------------- #
# GET /api/in_app_nudges — list undismissed banners for current_user
# --------------------------------------------------------------------------- #

@in_app_nudge_bp.route("", methods=["GET"])
@login_required
def list_undismissed():
    """Return undismissed banners for current_user as JSON.

    Response shape:
        {
          "banners": [
            {
              "id": 42,
              "rule_key": "inactive_3d",
              "headline": "Your remittance ledger is waiting",
              "body": "Haven't seen you in 3 days...",
              "cta_text": "Open my ledger",
              "cta_url": "https://fiesta-mvp.fly.dev/remittance/dashboard",
              "created_at": "2026-05-18T09:14:00"
            },
            ...
          ]
        }

    Emits `nudge_viewed` once per request that returned >= 1 banner.
    """
    from engagement_models import InAppBanner
    try:
        rows = (
            InAppBanner.query
                       .filter(InAppBanner.user_id == current_user.id,
                               InAppBanner.dismissed_at.is_(None))
                       .order_by(InAppBanner.created_at.desc())
                       .all()
        )
    except Exception as e:
        log.warning("GET /api/in_app_nudges DB error for user %s: %s",
                    getattr(current_user, "id", None), e)
        # Degrade gracefully: empty list is a valid response, the UI just
        # shows nothing. Better than a 500 that breaks the layout shell.
        return jsonify({"banners": []})

    out = [
        {
            "id": r.id,
            "rule_key": r.rule_key,
            "headline": r.headline,
            "body": r.body,
            "cta_text": r.cta_text,
            "cta_url": r.cta_url,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

    if out:
        # One emit per non-empty list — the UI renders all banners at once,
        # so counting per-banner views over-counts the impression signal.
        _safe_emit(
            "nudge_viewed",
            payload={
                "count": len(out),
                "rule_keys": [r["rule_key"] for r in out],
            },
        )

    return jsonify({"banners": out})


# --------------------------------------------------------------------------- #
# POST /api/in_app_nudges/<id>/dismiss — soft-delete
# --------------------------------------------------------------------------- #

@in_app_nudge_bp.route("/<int:banner_id>/dismiss", methods=["POST"])
@login_required
def dismiss(banner_id: int):
    """Set dismissed_at on the banner. Returns JSON {"ok": True}.

    Idempotent: a second dismiss is a no-op (we don't bump dismissed_at).
    """
    from app import db
    row = _load_banner_or_404(banner_id)

    if row.dismissed_at is None:
        try:
            row.dismissed_at = datetime.utcnow()
            db.session.commit()
        except Exception as e:
            log.warning("dismiss banner %s commit failed: %s", banner_id, e)
            try:
                db.session.rollback()
            except Exception:
                pass
            # Don't 500 the client — the banner is non-critical UI furniture.
            return jsonify({"ok": False, "error": "commit_failed"}), 500

    _safe_emit(
        "nudge_dismissed",
        payload={"banner_id": banner_id, "rule_key": row.rule_key},
    )
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# POST /api/in_app_nudges/<id>/click — emit + redirect
# --------------------------------------------------------------------------- #

@in_app_nudge_bp.route("/<int:banner_id>/click", methods=["POST"])
@login_required
def click(banner_id: int):
    """Emit nudge_clicked and redirect to the banner's cta_url.

    We use POST (not GET) so a prefetcher / link-preview crawler can't burn
    the conversion signal — clicks must be intentional. 302 redirect lets
    the browser handle the navigation as if the user clicked an anchor.
    """
    row = _load_banner_or_404(banner_id)
    _safe_emit(
        "nudge_clicked",
        payload={
            "banner_id": banner_id,
            "rule_key": row.rule_key,
            "cta_url": row.cta_url,
        },
    )
    return redirect(row.cta_url, code=302)


# --------------------------------------------------------------------------- #
# register_routes — wired from main.py by the orchestrator
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register the in_app_nudges blueprint on the Flask app.

    Same pattern as remittance_routes.register_routes and
    customer_brain_routes.register_routes. Called from main.py.
    """
    app.register_blueprint(in_app_nudge_bp)
    log.info("In-app nudge routes registered at /api/in_app_nudges/*")
