"""Persona routes — X2 cross-screen persona switch.

Endpoints
  GET  /persona              HTML page: current persona + v1.1 roadmap of future ones
  GET  /persona/current      JSON: { persona_id, name, locked, can_create_more }
  POST /persona/interest     Capture v1.1 waitlist signup for one persona_type

All routes require login. Side-effects are best-effort: analytics emission via
`events.emit` swallows failures (matches the project-wide pattern from Wave 2).
"""
import logging

from flask import Blueprint, jsonify, render_template, request, abort
from flask_login import current_user, login_required

from app import db
from .models import (
    Persona,
    PersonaInterest,
    PERSONA_TYPES,
    PERSONA_TYPE_SELF,
    PERSONA_LABELS,
    LOCKED_PERSONA_TYPES,
    current_persona,
    ensure_self_persona,
)

log = logging.getLogger(__name__)

persona_bp = Blueprint("persona", __name__, url_prefix="/persona")


def _emit_safe(event_type: str, **kwargs):
    """Best-effort analytics emit. Never raises."""
    try:
        from events import emit
        return emit(event_type, **kwargs)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("persona events.emit failed: %s", exc)
        return None


@persona_bp.route("", methods=["GET"])
@persona_bp.route("/", methods=["GET"])
@login_required
def persona_home():
    """Render the persona page — current persona + v1.1 roadmap."""
    me = ensure_self_persona(current_user)

    # Per-locked-type, has THIS user already registered interest?
    interest_rows = PersonaInterest.query.filter_by(user_id=current_user.id).all()
    interest_set = {r.persona_type for r in interest_rows}

    locked_with_state = [
        {
            "persona_type": pt,
            "label": PERSONA_LABELS[pt],
            "interest_captured": pt in interest_set,
        }
        for pt in LOCKED_PERSONA_TYPES
    ]

    _emit_safe(
        "persona_switcher_opened",
        user_id=current_user.id,
        source="route:persona_home",
        payload={"locked_types": LOCKED_PERSONA_TYPES},
    )

    return render_template(
        "persona/home.html",
        current_persona_row=me,
        locked_personas=locked_with_state,
        # Also expose the persona_types list for v1.1 forward-compat templates
        all_persona_types=PERSONA_TYPES,
    )


@persona_bp.route("/current", methods=["GET"])
@login_required
def persona_current():
    """JSON: the currently active persona for this user.

    Used by other screens to filter data. V1 always returns the 'self' persona.
    """
    me = current_persona(current_user)
    if me is None:
        # current_user.is_authenticated already enforced by @login_required, but
        # belt-and-braces: if for any reason current_persona returns None, signal
        # the client clearly rather than 500.
        return jsonify({"error": "no_persona"}), 404

    return jsonify(
        {
            "persona_id": me.persona_id,
            "name": me.display_label,
            "relationship": me.relationship,
            "active": me.active,
            "locked": me.persona_id in LOCKED_PERSONA_TYPES,
            "can_create_more": me.can_create_more,
        }
    )


@persona_bp.route("/interest", methods=["POST"])
@login_required
def persona_interest():
    """Capture v1.1 waitlist interest for a specific locked persona-type.

    Body: form-encoded OR JSON, key 'persona_type'.
    Idempotent: re-submitting for the same (user, persona_type) is a no-op
    (returns 200 with already_captured=True).
    """
    persona_type = (
        request.form.get("persona_type")
        or (request.get_json(silent=True) or {}).get("persona_type")
        or ""
    ).strip()

    # Only locked types are valid interest targets. Self is the active persona,
    # not a waitlist target.
    if persona_type not in LOCKED_PERSONA_TYPES:
        return jsonify({"error": "invalid_persona_type"}), 400

    existing = PersonaInterest.query.filter_by(
        user_id=current_user.id, persona_type=persona_type
    ).first()

    if existing is not None:
        # Idempotent return. We bump updated_at so we can see the most-recent
        # click time without creating duplicate rows.
        from datetime import datetime
        existing.updated_at = datetime.utcnow()
        db.session.commit()
        return jsonify(
            {"ok": True, "persona_type": persona_type, "already_captured": True}
        )

    row = PersonaInterest(
        user_id=current_user.id,
        persona_type=persona_type,
        email=getattr(current_user, "email", None),
    )
    db.session.add(row)
    db.session.commit()

    _emit_safe(
        "persona_v1_1_interest_captured",
        user_id=current_user.id,
        source="route:persona_interest",
        payload={"persona_type": persona_type},
    )

    return jsonify(
        {"ok": True, "persona_type": persona_type, "already_captured": False}
    )


def register_routes(app):
    """Standard FIESTA registration entry-point (matches remittance_routes pattern)."""
    app.register_blueprint(persona_bp)
    log.info("Persona routes registered at /persona/* (X2 v1 self-locked)")
