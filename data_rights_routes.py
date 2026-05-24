"""
data_rights_routes.py — F5 GDPR + SL PDPA data subject rights endpoints.

Tier D5 F5 compliance baseline (2026-05-24). Backs the rights enumerated in
templates/legal/privacy.html §5:

  - Access / portability  -> GET /api/me/data-export
                             Returns a JSON attachment with every row the
                             authenticated user can claim as theirs (User row,
                             subscriptions, deduction claims, rental
                             agreements, service providers, events).

  - Erasure               -> POST /api/me/delete
                             Confirmation-token gated soft-delete. Sets
                             User.deleted_at = utcnow(), anonymises name +
                             email to `deleted_user_<id>@deleted.fiesta`,
                             marks all paywall_subscription rows
                             status='cancelled'. Financial rows (receipts,
                             deduction claims, rental agreements) are RETAINED
                             — Sri Lankan IRA s.120 mandates a 5-year minimum
                             retention; we hold 7y operationally (see
                             privacy_policy.html §4).

  - UI surface            -> GET /account/data
                             Renders templates/account/data.html with both
                             export + delete forms.

Auth: all endpoints require flask_login.current_user.is_authenticated. The
POST /api/me/delete additionally requires CSRF + a session-issued
confirmation token (GET /api/me/delete-token).

PLACEHOLDER: the legal copy in privacy_policy.html / terms_of_service.html
is a draft (TOS_IS_DRAFT / PRIVACY_IS_DRAFT flags in fiesta/signup/version.py).
Counsel review pending per LEGAL_REVIEW_RETURN_DATE.
"""
from __future__ import annotations

import io
import json
import logging
import secrets
from datetime import datetime
from typing import Any, Dict, List

from flask import (
    Blueprint,
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    session,
)
from flask_login import current_user, login_required

log = logging.getLogger(__name__)


data_rights_bp = Blueprint(
    "data_rights",
    __name__,
    template_folder="templates",
)


_CONFIRMATION_SESSION_KEY = "data_rights_delete_token"
_CONFIRMATION_TTL_SECONDS = 600  # 10 minutes


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _serialise(value: Any) -> Any:
    """Best-effort JSON-safe serialisation for SQLAlchemy column values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat() + "Z"
    if isinstance(value, (list, tuple)):
        return [_serialise(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    # SQLAlchemy Numeric / Decimal / Date / Time fall back to str().
    try:
        return str(value)
    except Exception:
        return None


def _row_to_dict(row: Any) -> Dict[str, Any]:
    """Convert a SQLAlchemy model instance into a dict using its declared columns.

    Falls back to {} on any failure — data-export must never 500 because one
    row had an unserialisable column.
    """
    try:
        cols = row.__table__.columns.keys()
        return {c: _serialise(getattr(row, c, None)) for c in cols}
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("data_rights: row serialisation failed: %s", exc)
        return {}


def _safe_query_all(model, user_id: int) -> List[Dict[str, Any]]:
    """Query model.user_id == user_id and dict-ify; never raise."""
    try:
        rows = model.query.filter_by(user_id=user_id).all()  # type: ignore[attr-defined]
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        log.info(
            "data_rights: model %s unavailable for export (%s) -- skipping",
            getattr(model, "__name__", repr(model)),
            exc,
        )
        return []


def _gather_user_data(user) -> Dict[str, Any]:
    """Assemble the full export payload for a user.

    Each section is wrapped in try/except so a missing optional model
    (paywall, deductions, etc.) returns [] rather than 500ing the export.
    """
    payload: Dict[str, Any] = {
        "export_metadata": {
            "user_id": user.id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "format_version": "1.0",
            "sl_pdpa_reference": (
                "Personal Data Protection Act No. 9 of 2022 -- "
                "right of access + portability"
            ),
            "gdpr_reference": (
                "Regulation (EU) 2016/679 Articles 15 + 20 -- "
                "right of access + data portability"
            ),
            "retention_note": (
                "Financial records retained 7y per privacy_policy.html "
                "section 4 (SL IRA s.120 statutory floor 5y)."
            ),
        },
        "user": _row_to_dict(user),
    }

    # Subscriptions (paywall_subscription). Optional model — paywall package
    # may not be initialised in all environments.
    try:
        from fiesta.paywall.models import Subscription  # type: ignore
        if Subscription is not None:
            payload["subscriptions"] = _safe_query_all(Subscription, user.id)
        else:
            payload["subscriptions"] = []
    except Exception:
        payload["subscriptions"] = []

    # Deduction claims.
    try:
        from fiesta.deductions.models import DeductionClaim  # type: ignore
        payload["deduction_claims"] = _safe_query_all(DeductionClaim, user.id)
    except Exception:
        payload["deduction_claims"] = []

    # Rental agreements.
    try:
        from fiesta.property.models import RentalAgreement  # type: ignore
        payload["rental_agreements"] = _safe_query_all(RentalAgreement, user.id)
    except Exception:
        payload["rental_agreements"] = []

    # Service providers.
    try:
        from fiesta.service_providers.models import ServiceProvider  # type: ignore
        payload["service_providers"] = _safe_query_all(ServiceProvider, user.id)
    except Exception:
        payload["service_providers"] = []

    # Events attributable to the user.
    try:
        from event_models import Event  # type: ignore
        payload["events"] = _safe_query_all(Event, user.id)
    except Exception:
        payload["events"] = []

    return payload


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@data_rights_bp.route("/account/data", methods=["GET"])
@login_required
def account_data_page():
    """Settings page with Export + Delete controls (PDPA + GDPR rights surface)."""
    # Mint a fresh confirmation token for the delete form. The same value lands
    # in the rendered hidden field AND in the session, so the POST handler can
    # compare them. Rotated on every GET so a stale tab can't fire a delete.
    token = secrets.token_urlsafe(24)
    session[_CONFIRMATION_SESSION_KEY] = {
        "token": token,
        "issued_at": datetime.utcnow().isoformat() + "Z",
    }
    return render_template(
        "account/data.html",
        delete_confirmation_token=token,
    )


@data_rights_bp.route("/api/me/data-export", methods=["GET"])
@login_required
def data_export():
    """GDPR Art. 15+20 / PDPA s.13 -- right of access + portability.

    Returns the authenticated user's full data envelope as a JSON attachment.
    """
    if getattr(current_user, "deleted_at", None) is not None:
        return jsonify({"error": "account_deleted"}), 410

    payload = _gather_user_data(current_user)

    body = json.dumps(payload, indent=2, ensure_ascii=False)
    filename = f"fiesta-data-export-user-{current_user.id}.json"

    response = Response(body, mimetype="application/json")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response.headers["Cache-Control"] = "no-store"
    log.info(
        "data_rights: export served user_id=%s bytes=%s",
        current_user.id,
        len(body.encode("utf-8")),
    )
    return response


@data_rights_bp.route("/api/me/delete", methods=["POST"])
@login_required
def data_delete():
    """GDPR Art. 17 / PDPA s.16 -- right to erasure (soft-delete).

    Requires a session-issued confirmation token (set by GET /account/data).
    Financial rows are RETAINED per SL IRA s.120 (see privacy_policy.html
    section 4). PII (name, email) is anonymised so the user no longer
    appears in active queries.
    """
    if getattr(current_user, "deleted_at", None) is not None:
        return jsonify({"error": "already_deleted"}), 410

    # Confirmation token comes from form OR JSON body.
    submitted = (
        request.form.get("confirmation_token")
        or (request.get_json(silent=True) or {}).get("confirmation_token")
    )
    stored = session.get(_CONFIRMATION_SESSION_KEY) or {}
    expected = stored.get("token")

    if not submitted or not expected or submitted != expected:
        return (
            jsonify(
                {
                    "error": "confirmation_required",
                    "message": (
                        "Account deletion requires a fresh confirmation "
                        "token. Visit /account/data, click 'Delete my "
                        "account', and submit the form on that page."
                    ),
                }
            ),
            400,
        )

    # One-shot: invalidate the token immediately so the same token can't
    # delete twice.
    session.pop(_CONFIRMATION_SESSION_KEY, None)

    try:
        from app import db
    except Exception as exc:  # pragma: no cover — defensive
        log.error("data_rights: db import failed: %s", exc)
        return jsonify({"error": "internal_error"}), 500

    user_id = current_user.id
    original_email = current_user.email

    # Anonymise PII.
    current_user.name = f"deleted_user_{user_id}"
    current_user.email = f"deleted_user_{user_id}@deleted.fiesta"
    current_user.deleted_at = datetime.utcnow()
    # Tear down auth handles so the soft-deleted row can't be used to log back in.
    current_user.password_hash = None
    current_user.social_id = None
    current_user.social_provider = None
    current_user.is_email_verified = False
    current_user.email_verification_token = None
    current_user.email_verification_salt = None
    current_user.subscription_status = "cancelled"

    # Cancel active paywall subscriptions (best effort — soft-delete must not
    # fail if the paywall package isn't loaded).
    cancelled_subscriptions = 0
    try:
        from fiesta.paywall.models import Subscription  # type: ignore
        if Subscription is not None:
            rows = Subscription.query.filter_by(user_id=user_id).all()  # type: ignore[attr-defined]
            for row in rows:
                if getattr(row, "status", None) != "cancelled":
                    row.status = "cancelled"
                    if hasattr(row, "cancel_at_period_end"):
                        row.cancel_at_period_end = True
                    cancelled_subscriptions += 1
    except Exception as exc:
        log.warning(
            "data_rights: subscription cancellation skipped (%s)", exc,
        )

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log.error(
            "data_rights: delete commit failed user_id=%s err=%s",
            user_id,
            exc,
        )
        return jsonify({"error": "internal_error"}), 500

    log.info(
        "data_rights: soft-delete user_id=%s original_email=%s "
        "cancelled_subscriptions=%s",
        user_id,
        original_email,
        cancelled_subscriptions,
    )

    # Log the user out so the surviving session can't keep acting as the
    # anonymised row.
    try:
        from flask_login import logout_user
        logout_user()
    except Exception:
        pass

    return jsonify(
        {
            "status": "deleted",
            "user_id": user_id,
            "deleted_at": datetime.utcnow().isoformat() + "Z",
            "subscriptions_cancelled": cancelled_subscriptions,
            "retention_note": (
                "Financial records retained 7y per SL IRA s.120 / "
                "privacy_policy.html section 4."
            ),
        }
    ), 200


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook called from main.py."""
    if "data_rights" in app.blueprints:
        log.debug("data_rights blueprint already registered -- skipping.")
        return
    app.register_blueprint(data_rights_bp)
    log.info(
        "data_rights blueprint registered: /account/data, "
        "/api/me/data-export, /api/me/delete"
    )
