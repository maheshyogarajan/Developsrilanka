"""S3 Profile blueprint — progressive disclosure routes.

Mount point: /fie/profile  (A4 F2.6; /fiesta/profile is a 302 alias via bp_legacy)

Routes:
- GET  /fie/profile                      — render the profile form
- POST /fie/profile                      — persist (full form OR single field auto-save)
- GET  /fie/profile/progress             — JSON for the dashboard widget
- GET  /fie/profile/check/<field>        — async field availability check (NIC dedup)
- GET  /fie/profile/required-for/<scr>   — JSON: missing fields blocking screen <scr>

Voice: empowerment ("Help us help you") not corporate-form. See template strings.
Auth: all routes require @login_required. Per-user single profile row enforced by
the unique constraint on FiestaProfile.user_id.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from pydantic import ValidationError

from .models import FiestaProfile, get_or_create_profile
from .progressive import (
    ALL_PROFILE_FIELDS,
    SCREEN_REQUIREMENTS,
    SL_BANKS,
    base_profile_complete,
    progress_pct,
    required_for_screen,
    section_progress,
)
from .validators import ProfileFormPayload

logger = logging.getLogger(__name__)

# A4 F2.6 — Primary mount point is now /fie/profile (URL-space parity with
# /fie/triage). The old /fiesta/profile prefix is kept as a 302 alias via the
# redirect blueprint below.
bp = Blueprint(
    "fiesta_profile",
    __name__,
    url_prefix="/fie/profile",
    template_folder="../../templates",
)

# 302-alias blueprint: /fiesta/profile/* → /fie/profile/*
bp_legacy = Blueprint(
    "fiesta_profile_legacy",
    __name__,
    url_prefix="/fiesta/profile",
)


# ---------------------------------------------------------------------------
# Analytics emit helper — soft-dependency on whatever analytics surface exists
# ---------------------------------------------------------------------------


def _emit_event(event_name: str, **props: Any) -> None:
    """Emit an analytics event. No-op if no backend is configured.

    FIESTA has no canonical analytics layer yet (Wave 5+). For now we log to a
    dedicated logger so events are captured in app logs and can be tail-piped
    into PostHog / Mixpanel later without code changes.
    """
    payload = {"event": event_name, **props}
    try:
        logging.getLogger("fiesta.analytics").info(json.dumps(payload, default=str))
    except Exception:  # noqa: BLE001
        # Never let analytics break a user request.
        pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("", methods=["GET"], strict_slashes=False)
@bp.route("/", methods=["GET"], strict_slashes=False)
@login_required
def index():
    """Render the profile form with the user's current values.

    A5 F2.8 — Triage-completion gate: if the user hasn't answered the 3
    quick triage questions yet (triage_answers is None or empty), flash a
    friendly prompt and redirect to /fie/triage. The profile only makes
    sense after the triage persona-detection is complete.
    """
    # A5 F2.8 — Gate: triage must be complete before the profile is accessible.
    triage_answers = getattr(current_user, 'triage_answers', None)
    if not triage_answers:
        flash("Let's start with 3 quick questions", "info")
        return redirect("/fie/triage")

    profile = get_or_create_profile(current_user.id)
    sections = section_progress(profile)
    total_pct = progress_pct(profile)
    return render_template(
        "profile/index.html",
        profile=profile,
        sections=sections,
        total_pct=total_pct,
        sl_banks=SL_BANKS,
        base_complete=base_profile_complete(profile),
    )


@bp.route("", methods=["POST"], strict_slashes=False)
@bp.route("/", methods=["POST"], strict_slashes=False)
@login_required
def save():
    """Persist profile updates. Supports two modes:

    1. Full form POST (Content-Type: application/x-www-form-urlencoded)
       — validates the whole payload and redirects back to the form.

    2. Auto-save POST (Content-Type: application/json, single field per call)
       — validates ONLY supplied fields and returns JSON.
    """
    profile = get_or_create_profile(current_user.id)
    from app import db  # local import — avoid circular

    is_json = request.is_json or request.headers.get("X-Auto-Save") == "1"
    raw_data: Dict[str, Any]
    if is_json:
        raw_data = request.get_json(silent=True) or {}
    else:
        raw_data = {k: v for k, v in request.form.items()}

    # Coerce empty strings to None so pydantic optional defaults kick in cleanly.
    cleaned: Dict[str, Any] = {}
    for k, v in raw_data.items():
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                continue
        # Special coercions for HTML form bools / ints
        if k == "has_foreign_clients" and isinstance(v, str):
            v = v.lower() in {"true", "1", "yes", "on"}
        if k in {"tax_resident_year", "days_in_sl_current_year"} and isinstance(v, str):
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
        cleaned[k] = v

    try:
        payload = ProfileFormPayload(**cleaned)
    except ValidationError as exc:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]}
            for e in exc.errors()
        ]
        _emit_event(
            "profile_validation_error",
            user_id=current_user.id,
            fields=[e["field"] for e in errors],
        )
        if is_json:
            return jsonify({"ok": False, "errors": errors}), 422
        for err in errors:
            flash(f"{err['field']}: {err['msg']}", "danger")
        return redirect(url_for("fiesta_profile.index"))

    # Apply validated payload
    before = profile.to_dict(redact_bank=False)
    profile.apply(payload.model_dump(exclude_none=True))
    db.session.commit()
    after = profile.to_dict(redact_bank=False)

    # Per-field completion events
    for field in payload.model_fields_set:
        if before.get(field) != after.get(field):
            _emit_event(
                "profile_field_completed",
                user_id=current_user.id,
                field=field,
            )

    # Per-section completion events
    for section, fields in ALL_PROFILE_FIELDS.items():
        was_complete = all(before.get(f) not in (None, "") for f in fields)
        now_complete = all(after.get(f) not in (None, "") for f in fields)
        if not was_complete and now_complete:
            _emit_event(
                "profile_section_complete",
                user_id=current_user.id,
                section=section,
            )

    if is_json:
        return jsonify(
            {
                "ok": True,
                "progress_pct": progress_pct(profile),
                "sections": section_progress(profile),
                "profile": profile.to_dict(),
            }
        )

    flash("Profile saved. Thanks — that helps us help you better.", "success")
    next_url = request.args.get("next")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("fiesta_profile.index"))


@bp.route("/progress", methods=["GET"])
@login_required
def progress():
    """JSON endpoint for the dashboard widget. Returns total + per-section pct."""
    profile = get_or_create_profile(current_user.id)
    return jsonify(
        {
            "ok": True,
            "progress_pct": progress_pct(profile),
            "sections": section_progress(profile),
            "base_complete": base_profile_complete(profile),
        }
    )


@bp.route("/check/<field>", methods=["GET"])
@login_required
def check(field: str):
    """Async field availability / format check.

    v1 supports: nic (duplicate detection). Other fields return ok=True so the UI
    can call /check/<anything> safely while we expand coverage.
    """
    value = (request.args.get("value") or "").strip()
    if not value:
        return jsonify({"ok": True, "available": True, "msg": "empty"})

    if field == "nic":
        from .validators import validate_nic, NICValidationError

        try:
            normalized = validate_nic(value)
        except NICValidationError as exc:
            return jsonify({"ok": False, "msg": str(exc)}), 200
        existing = (
            FiestaProfile.query.filter(FiestaProfile.nic == normalized)
            .filter(FiestaProfile.user_id != current_user.id)
            .first()
        )
        if existing:
            _emit_event(
                "profile_nic_duplicate_detected",
                user_id=current_user.id,
                nic=normalized,
            )
            return jsonify(
                {
                    "ok": False,
                    "available": False,
                    "msg": (
                        "This NIC is already on another FIESTA account. "
                        "If you think that's a mistake, contact support."
                    ),
                }
            )
        return jsonify({"ok": True, "available": True, "msg": "available"})

    # Unknown field — fail-open with a soft signal so the client can fall back.
    return jsonify({"ok": True, "available": True, "msg": "no-check-defined"})


@bp.route("/required-for/<screen_id>", methods=["GET"])
@login_required
def required_for(screen_id: str):
    """Return the missing-required-fields for a downstream screen.

    Used by screens like /fiesta/earnings to decide whether to render their UI
    or redirect to /fiesta/profile?next=/fiesta/earnings.
    """
    if screen_id not in SCREEN_REQUIREMENTS:
        return jsonify({"ok": False, "error": f"unknown screen: {screen_id}"}), 404

    profile = get_or_create_profile(current_user.id)
    missing = required_for_screen(screen_id, profile)

    if missing:
        _emit_event(
            f"profile_required_for_screen_{screen_id}_missing",
            user_id=current_user.id,
            missing_fields=missing,
        )

    return jsonify(
        {
            "ok": True,
            "screen": screen_id,
            "missing": missing,
            "can_proceed": len(missing) == 0,
            "profile_url": url_for(
                "fiesta_profile.index", next=request.args.get("next") or ""
            ),
        }
    )


# ---------------------------------------------------------------------------
# Legacy /fiesta/profile → /fie/profile 302 redirects (A4 F2.6)
# ---------------------------------------------------------------------------


@bp_legacy.route("", methods=["GET", "POST"], strict_slashes=False)
@bp_legacy.route("/", methods=["GET", "POST"], strict_slashes=False)
def _legacy_index():
    """302 redirect: /fiesta/profile → /fie/profile"""
    from flask import redirect, url_for as _url_for, request as _req
    target = "/fie/profile"
    qs = _req.query_string.decode("utf-8")
    if qs:
        target = f"{target}?{qs}"
    return redirect(target, code=302)


@bp_legacy.route("/<path:subpath>", methods=["GET", "POST"])
def _legacy_subpath(subpath: str):
    """302 redirect: /fiesta/profile/<subpath> → /fie/profile/<subpath>"""
    from flask import redirect, request as _req
    target = f"/fie/profile/{subpath}"
    qs = _req.query_string.decode("utf-8")
    if qs:
        target = f"{target}?{qs}"
    return redirect(target, code=302)


# ---------------------------------------------------------------------------
# Blueprint registration helper — mirrors fiesta/signup pattern
# ---------------------------------------------------------------------------


def register_blueprint(app) -> None:
    """Register the S3 profile blueprint and run the idempotent migration."""
    from .models import migrate as profile_migrate

    if "fiesta_profile" in app.blueprints:
        logger.info("[fiesta.profile] blueprint already registered, skipping")
        return

    app.register_blueprint(bp)
    # A4 F2.6 — also register the legacy 302-redirect blueprint
    app.register_blueprint(bp_legacy)
    summary = profile_migrate(app)
    logger.info("[fiesta.profile] registered at /fie/profile + legacy 302 at /fiesta/profile: %s", summary)


__all__ = ["bp", "bp_legacy", "register_blueprint"]
