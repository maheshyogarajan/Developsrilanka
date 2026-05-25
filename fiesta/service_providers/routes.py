"""fiesta.service_providers.routes — Flask blueprint for S6.

Routes:
    GET    /service-providers
        List view (cards) of all the user's active SPs + an empty-state
        for first-time visitors.

    POST   /service-providers
        Add a new SP. Returns:
          - 303 redirect to /service-providers (HTML form submit), OR
          - 201 JSON {sp, relationship} (AJAX submit with `Accept: application/json`).
        Inline-runs §195 detection on the new record and persists.

    GET    /service-providers/<id>
        Single SP detail (modal partial OR full page).

    PUT    /service-providers/<id>
        Edit. Re-runs §195 detection after applying changes.

    DELETE /service-providers/<id>
        Soft-archive. Never hard-delete; downstream audit trail.

    POST   /service-providers/<id>/re-detect
        Re-run §195 detection (e.g. after the customer adds bank info).

    GET    /service-providers/related-party-signals/<id>
        JSON with the full reasoning trace for audit-defensibility.

    POST   /service-providers/<id>/override-disclosure
        Customer disagrees with the default-on disclosure. Requires a
        commercial-substance justification (free text). Tier-1 grade
        decision — recorded on the relationship row.

CSRF: Flask-WTF expects a token. The HTML form uses the meta tag from
layout.html. JSON endpoints accept the X-CSRF-Token header.

Auth: All routes require @login_required.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports (mirrors the deductions blueprint pattern so this
# module imports cleanly in pure-unit-test envs without Flask).
# ---------------------------------------------------------------------------
try:
    from flask import (
        Blueprint, render_template, request, jsonify, redirect, url_for,
        flash, session, abort,
    )
    _HAS_FLASK = True
except ImportError:  # pragma: no cover
    _HAS_FLASK = False

    class _Stub:
        def __init__(self, *a, **kw): pass
        def route(self, *a, **kw):
            def deco(fn): return fn
            return deco

    class Blueprint(_Stub):  # type: ignore
        pass

    def render_template(*a, **kw): return ""  # type: ignore
    def jsonify(*a, **kw): return {"_stub": True}  # type: ignore
    def redirect(*a, **kw): return None  # type: ignore
    def url_for(*a, **kw): return "#"  # type: ignore
    def flash(*a, **kw): return None  # type: ignore
    def abort(*a, **kw): return None  # type: ignore

    class _SessionStub(dict):
        def get(self, k, default=None): return None
    session = _SessionStub()  # type: ignore
    request = None  # type: ignore

try:
    from flask_login import login_required, current_user
    _HAS_LOGIN = True
except ImportError:  # pragma: no cover
    _HAS_LOGIN = False

    def login_required(fn):  # type: ignore
        return fn
    current_user = None  # type: ignore

try:
    from fiesta.paywall.gate import paywall_required
    _HAS_PAYWALL = True
except ImportError:  # pragma: no cover
    _HAS_PAYWALL = False

    def paywall_required(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco

try:
    from app import db
    _HAS_DB = True
except Exception:  # pragma: no cover
    _HAS_DB = False
    db = None  # type: ignore

from fiesta.service_providers.models import (
    ServiceProvider,
    ServiceProviderRelationship,
    SERVICE_TYPE_CATALOG,
    SERVICE_TYPE_IDS,
    STATED_RELATIONSHIP_CHOICES,
    STATED_RELATIONSHIP_IDS,
    FEE_STRUCTURE_CHOICES,
    FEE_STRUCTURE_IDS,
)
from fiesta.service_providers.related_party import (
    run_detection_for_sp,
    persist_detection_result,
)


service_providers_bp = Blueprint(
    "fiesta_service_providers",
    __name__,
    url_prefix="/service-providers",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _invalidate_agreement_cache_for(user_id: Optional[int], sp_id: Optional[int]) -> None:
    """Tier D6 / D8 — drop the cached `protected_deductions_lkr` + SP-object
    entries used by `/agreements/service/<sp_id>` after any SP write.
    Best-effort, never raises.
    """
    if not user_id:
        return
    try:
        from fiesta.agreements.service_routes import invalidate_service_agreement_cache
        invalidate_service_agreement_cache(
            user_id, str(sp_id) if sp_id is not None else None
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("agreement cache invalidate skipped: %s", exc)


def _current_user_id() -> Optional[int]:
    if not _HAS_LOGIN or current_user is None:
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    return getattr(current_user, "id", None)


def _wants_json() -> bool:
    if not _HAS_FLASK or request is None:
        return False
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.is_json


def _decimal_or_none(raw: Any) -> Optional[Decimal]:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_sp_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize incoming form/JSON payload to model-ready kwargs.

    Returns (cleaned, errors). The cleaned dict is suitable for
    SP.__init__ (excluding user_id). The errors list contains
    human-readable validation messages. Empty errors == OK.
    """
    errors: list[str] = []
    cleaned: dict[str, Any] = {}

    name = (payload.get("name") or "").strip()
    if not name:
        errors.append("Name is required.")
    cleaned["name"] = name[:255]

    service_type = (payload.get("service_type") or "").strip()
    if service_type not in SERVICE_TYPE_IDS:
        errors.append("Pick a service type.")
    cleaned["service_type"] = service_type

    fee_structure = (payload.get("fee_structure") or "monthly").strip()
    if fee_structure not in FEE_STRUCTURE_IDS:
        errors.append("Pick a fee structure.")
    cleaned["fee_structure"] = fee_structure

    stated = (payload.get("stated_relationship_to_customer") or "professional_arms_length").strip()
    if stated not in STATED_RELATIONSHIP_IDS:
        errors.append("Pick how you know this person.")
    cleaned["stated_relationship_to_customer"] = stated

    # Optional fields — all strings stored verbatim, no validation.
    for key in (
        "nic", "tin", "address_line1", "address_line2", "city", "country",
        "postcode", "bank_name", "bank_account_number", "notes",
    ):
        raw = payload.get(key)
        if raw is None:
            continue
        s = str(raw).strip()
        cleaned[key] = s or None

    # Money — accept either hourly or monthly; both optional.
    cleaned["hourly_rate"] = _decimal_or_none(payload.get("hourly_rate"))
    cleaned["monthly_rate"] = _decimal_or_none(payload.get("monthly_rate"))

    return cleaned, errors


def _apply_payload_to_sp(sp: ServiceProvider, cleaned: dict[str, Any]) -> None:
    """Apply a cleaned payload to an SP instance."""
    # Money fields go through the property setters (they handle cents
    # conversion). Pull them out before bulk-assigning the rest.
    hourly = cleaned.pop("hourly_rate", None)
    monthly = cleaned.pop("monthly_rate", None)
    sp.hourly_rate = hourly  # property setter
    sp.monthly_rate = monthly  # property setter

    for k, v in cleaned.items():
        if hasattr(sp, k):
            setattr(sp, k, v)


# ---------------------------------------------------------------------------
# Total-paid-YTD recomputation.
#
# Per spec: total_paid_this_year is recomputed on every read, not stored.
# In FIESTA today there isn't yet a per-SP payment ledger, so this returns
# zero — but the call-site is wired so when the ledger lands it just
# needs to be implemented here.
# ---------------------------------------------------------------------------
def _compute_total_paid_ytd(user_id: int, sp_id: int) -> Decimal:
    """Recompute year-to-date total paid to this SP. Currently returns 0."""
    return Decimal("0.00")


# ---------------------------------------------------------------------------
# Routes.
# ---------------------------------------------------------------------------
@service_providers_bp.route("", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="index")
def index():
    """List view — cards for every active SP, with the §195 banner per-card."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)

    sps = (
        ServiceProvider.query
        .filter_by(user_id=user_id, archived=False)
        .order_by(ServiceProvider.created_at.desc())
        .all()
    )
    rel_by_sp = {
        r.sp_id: r
        for r in ServiceProviderRelationship.query
        .filter(ServiceProviderRelationship.sp_id.in_([s.id for s in sps] or [-1]))
        .all()
    }

    cards = []
    for sp in sps:
        rel = rel_by_sp.get(sp.id)
        cards.append({
            "sp": sp,
            "relationship": rel,
            "total_paid_ytd": _compute_total_paid_ytd(user_id, sp.id),
            "service_type_label": _service_type_label(sp.service_type),
        })

    return render_template(
        "service_providers/index.html",
        cards=cards,
        service_type_catalog=SERVICE_TYPE_CATALOG,
        stated_relationship_choices=STATED_RELATIONSHIP_CHOICES,
        fee_structure_choices=FEE_STRUCTURE_CHOICES,
    )


@service_providers_bp.route("", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="create")
def create():
    """Add a new SP and run §195 inline."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    cleaned, errors = _parse_sp_payload(payload or {})
    if errors:
        if _wants_json():
            return jsonify({"ok": False, "errors": errors}), 400
        for e in errors:
            flash(e, "error")
        return redirect(url_for("fiesta_service_providers.index"))

    sp = ServiceProvider(user_id=user_id)
    _apply_payload_to_sp(sp, cleaned)
    db.session.add(sp)
    db.session.flush()  # populate sp.id for the relationship row

    # Inline §195 detection.
    try:
        result = run_detection_for_sp(sp, payments=None)
        rel = persist_detection_result(sp, result, db_session=db.session)
    except Exception as exc:
        # Fail closed on the disclosure flag — the binding contract is that
        # underdetection is forbidden. If detection itself errors, default
        # the SP to requires_disclosure=True so S8 won't ship without a
        # human review.
        logger.exception("§195 detection failed for new sp; failing closed: %s", exc)
        sp.requires_disclosure = True
        rel = ServiceProviderRelationship(
            sp_id=sp.id,
            user_id=user_id,
            signals=[],
            confidence=0.0,
            should_default_on_disclosure=True,
            audit_substance_risk="high",
            reasoning=[
                "detector unavailable at creation time — defaulting "
                "disclosure ON until a successful re-detection."
            ],
            last_detected_at=datetime.utcnow(),
        )
        db.session.add(rel)

    db.session.commit()
    _invalidate_agreement_cache_for(user_id, sp.id)

    if _wants_json():
        return jsonify({
            "ok": True,
            "sp": sp.to_dict(),
            "relationship": rel.to_dict() if rel else None,
        }), 201

    if sp.requires_disclosure:
        flash(
            "Service provider added. Heads up: based on what you shared, "
            "we'll include the §195 disclosure when we generate the agreement.",
            "info",
        )
    else:
        flash("Service provider added.", "success")
    return redirect(url_for("fiesta_service_providers.index"))


@service_providers_bp.route("/<int:sp_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="show")
def show(sp_id: int):
    """Single SP detail."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)

    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)

    rel = ServiceProviderRelationship.query.filter_by(sp_id=sp.id).first()
    payload = {
        "sp": sp.to_dict(),
        "relationship": rel.to_dict() if rel else None,
        "total_paid_ytd": str(_compute_total_paid_ytd(user_id, sp.id)),
        "service_type_label": _service_type_label(sp.service_type),
    }
    if _wants_json():
        return jsonify(payload)
    return render_template(
        "service_providers/edit.html",
        sp=sp, relationship=rel,
        service_type_catalog=SERVICE_TYPE_CATALOG,
        stated_relationship_choices=STATED_RELATIONSHIP_CHOICES,
        fee_structure_choices=FEE_STRUCTURE_CHOICES,
    )


@service_providers_bp.route("/<int:sp_id>", methods=["PUT", "POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="update")
def update(sp_id: int):
    """Edit. PUT for AJAX, POST for HTML form submit.

    Routes whose method is POST + has the conventional `_method=PUT`
    hidden field also land here (we accept POST as an alias to support
    classic form posts without JS).
    """
    user_id = _current_user_id()
    if user_id is None:
        abort(401)

    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)

    # Reject classic-form POSTs unless they declare PUT explicitly. This
    # avoids accidentally double-handling /service-providers POST (create)
    # vs /service-providers/<id> POST (update).
    if request.method == "POST":
        method_override = (
            (request.form.get("_method") if request.form else None)
            or request.headers.get("X-HTTP-Method-Override", "")
        )
        if method_override.upper() != "PUT":
            abort(405)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    cleaned, errors = _parse_sp_payload(payload or {})
    if errors:
        if _wants_json():
            return jsonify({"ok": False, "errors": errors}), 400
        for e in errors:
            flash(e, "error")
        return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))

    _apply_payload_to_sp(sp, cleaned)

    # Always re-detect on edit — fields that affect signals may have changed.
    try:
        result = run_detection_for_sp(sp, payments=None)
        rel = persist_detection_result(sp, result, db_session=db.session)
    except Exception as exc:
        logger.exception(
            "§195 re-detection failed on update; failing closed: %s", exc
        )
        sp.requires_disclosure = True
        rel = ServiceProviderRelationship.query.filter_by(sp_id=sp.id).first()

    db.session.commit()
    _invalidate_agreement_cache_for(user_id, sp.id)

    if _wants_json():
        return jsonify({
            "ok": True,
            "sp": sp.to_dict(),
            "relationship": rel.to_dict() if rel else None,
        })

    flash("Saved.", "success")
    return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))


@service_providers_bp.route("/<int:sp_id>", methods=["DELETE"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="archive")
def archive(sp_id: int):
    """Soft-archive (never hard-delete — audit-trail retention)."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)
    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)
    sp.archived = True
    db.session.commit()
    _invalidate_agreement_cache_for(user_id, sp.id)
    if _wants_json():
        return jsonify({"ok": True, "archived": True})
    flash("Service provider archived.", "success")
    return redirect(url_for("fiesta_service_providers.index"))


@service_providers_bp.route("/<int:sp_id>/re-detect", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="re_detect")
def re_detect(sp_id: int):
    """Re-run §195 detection (e.g. after the customer adds bank info)."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)
    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)

    result = run_detection_for_sp(sp, payments=None)
    rel = persist_detection_result(sp, result, db_session=db.session)
    db.session.commit()
    _invalidate_agreement_cache_for(user_id, sp.id)

    if _wants_json():
        return jsonify({
            "ok": True,
            "sp": sp.to_dict(),
            "relationship": rel.to_dict() if rel else None,
        })
    flash("Re-checked. Disclosure setting updated.", "success")
    return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))


@service_providers_bp.route(
    "/related-party-signals/<int:sp_id>", methods=["GET"]
)
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="signals_json")
def signals_json(sp_id: int):
    """JSON: full §195 reasoning trace for audit-defensibility surface."""
    user_id = _current_user_id()
    if user_id is None:
        abort(401)
    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)
    rel = ServiceProviderRelationship.query.filter_by(sp_id=sp.id).first()
    if rel is None:
        # Lazy-trigger so the trace is always available.
        result = run_detection_for_sp(sp, payments=None)
        rel = persist_detection_result(sp, result, db_session=db.session)
        db.session.commit()
    return jsonify({
        "sp_id": sp.id,
        "sp_name": sp.name,
        "signals": rel.signals or [],
        "confidence": rel.confidence,
        "should_default_on_disclosure": rel.should_default_on_disclosure,
        "audit_substance_risk": rel.audit_substance_risk,
        "reasoning": rel.reasoning or [],
        "customer_disclosure_override": rel.customer_disclosure_override,
        "override_justification": rel.override_justification,
        "effective_disclosure_required": rel.effective_disclosure_required,
        "last_detected_at": (
            rel.last_detected_at.isoformat() if rel.last_detected_at else None
        ),
    })


@service_providers_bp.route(
    "/<int:sp_id>/override-disclosure", methods=["POST"]
)
@login_required
@paywall_required(min_tier="self_file", screen_id="S6", action="override_disclosure")
def override_disclosure(sp_id: int):
    """Customer overrides the default-on disclosure flag.

    Body (JSON or form): {
        "override": True | False,        # what they want the flag set to
        "justification": "free text"     # commercial-substance reason
    }

    The override is recorded but is NOT a license to silently strip the
    §195 disclosure if the detector is screaming — we persist BOTH the
    override and the detector's recommendation so the S8 generator can
    embed an "auditor's note" in the agreement if there's daylight.
    """
    user_id = _current_user_id()
    if user_id is None:
        abort(401)
    sp = ServiceProvider.query.filter_by(id=sp_id, user_id=user_id).first()
    if sp is None:
        abort(404)
    rel = ServiceProviderRelationship.query.filter_by(sp_id=sp.id).first()
    if rel is None:
        # Lazy-trigger so we have a row to override.
        result = run_detection_for_sp(sp, payments=None)
        rel = persist_detection_result(sp, result, db_session=db.session)

    payload = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    override_raw = (payload or {}).get("override")
    justification = ((payload or {}).get("justification") or "").strip()

    if override_raw in (None, ""):
        if _wants_json():
            return jsonify({"ok": False, "errors": ["override required"]}), 400
        flash("Pick whether to include or skip the disclosure.", "error")
        return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))

    override_bool = str(override_raw).lower() in {"true", "1", "yes", "on"}

    # If the customer is DISABLING a default-on disclosure, require a
    # justification. If they're confirming, justification is optional.
    if not override_bool and rel.should_default_on_disclosure and len(justification) < 20:
        msg = (
            "When skipping a disclosure we recommended, please add a short "
            "note (at least 20 characters) explaining why this is arm's "
            "length — auditors look for commercial-substance evidence."
        )
        if _wants_json():
            return jsonify({"ok": False, "errors": [msg]}), 400
        flash(msg, "error")
        return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))

    rel.customer_disclosure_override = override_bool
    rel.override_justification = justification or None
    rel.override_set_at = datetime.utcnow()
    # Re-sync the denormalised flag on the SP.
    sp.requires_disclosure = rel.effective_disclosure_required
    db.session.commit()
    _invalidate_agreement_cache_for(user_id, sp.id)

    if _wants_json():
        return jsonify({
            "ok": True,
            "relationship": rel.to_dict(),
            "sp_requires_disclosure": sp.requires_disclosure,
        })
    flash("Saved.", "success")
    return redirect(url_for("fiesta_service_providers.show", sp_id=sp.id))


# ---------------------------------------------------------------------------
# Small lookup helper.
# ---------------------------------------------------------------------------
def _service_type_label(service_type_id: str) -> str:
    for entry in SERVICE_TYPE_CATALOG:
        if entry["id"] == service_type_id:
            return entry["name"]
    return service_type_id or "(unspecified)"


# ---------------------------------------------------------------------------
# Blueprint registrar (called from main.py).
# ---------------------------------------------------------------------------
def register_blueprint(app: Any) -> None:
    """Idempotent blueprint registration."""
    if not _HAS_FLASK:  # pragma: no cover
        return
    # Skip if already registered (defensive — gunicorn workers may re-import).
    existing = {bp.name for bp in app.blueprints.values()}
    if "fiesta_service_providers" in existing:
        return
    app.register_blueprint(service_providers_bp)
