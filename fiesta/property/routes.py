"""fiesta.property.routes — Flask blueprint for S7 Property Owner.

Routes (all login-gated):
    GET  /property                          — list user properties
    POST /property                          — add property
    GET  /property/<id>                     — property detail
    PUT  /property/<id>                     — edit property
    GET  /property/<id>/landlord            — landlord detail
    POST /property/<id>/landlord            — add/edit landlord (triggers §195)
    GET  /property/<id>/rental               — rental agreement detail
    POST /property/<id>/rental               — add/edit rental agreement
    GET  /property/<id>/rental/preview      — pre-S9 preview of agreement
    GET  /property/<id>/sanity-check        — market-rate + home-office check
    POST /property/<id>/prefill-prior-year  — copy last year's rental forward

CSRF: layout.html exposes csrf_token() via meta tag. JS posts use that.

Auth: All routes require @login_required.

Persistence: Property + Landlord + RentalAgreement + LandlordRelationshipDetection.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defensive imports (mirrors fiesta.deductions.routes)
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

from .sanity import run_sanity_checks
from . import related_party as rp


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
property_bp = Blueprint(
    "fiesta_property",
    __name__,
    url_prefix="/property",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _invalidate_rental_cache_for(user_id: int | None, property_id: int | None) -> None:
    """Tier D6 / D8 — drop the cached rental-bundle + projection entries
    used by `/agreements/rental/<property_id>` after any Property / Landlord /
    RentalAgreement write. Best-effort, never raises.
    """
    if not user_id:
        return
    try:
        from fiesta.agreements.rental_routes import invalidate_rental_agreement_cache
        invalidate_rental_agreement_cache(user_id, property_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("rental agreement cache invalidate skipped: %s", exc)


def _current_user_id() -> int | None:
    if not _HAS_LOGIN or current_user is None:
        return None
    try:
        if not getattr(current_user, "is_authenticated", False):
            return None
        return int(getattr(current_user, "id", 0)) or None
    except (TypeError, ValueError):
        return None


def _current_user_profile() -> dict[str, Any]:
    """Minimal profile dict passed into the §195 detector."""
    if not _HAS_LOGIN or current_user is None:
        return {}
    try:
        if not getattr(current_user, "is_authenticated", False):
            return {}
    except Exception:
        return {}
    full_name = (
        getattr(current_user, "full_name", None)
        or getattr(current_user, "name", None)
        or getattr(current_user, "username", None)
        or ""
    )
    return {
        "full_name": full_name,
        "nic": getattr(current_user, "nic", None),
        "bank_account": getattr(current_user, "bank_account_number", None),
        "address": getattr(current_user, "address", None),
    }


def _resolve_tax_year() -> str:
    try:
        ty = session.get("tax_year")
        if ty:
            return str(ty)
    except RuntimeError:
        pass
    return "2025/2026"


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw in (None, "", "null"):
        return None
    try:
        d = Decimal(str(raw))
    except InvalidOperation:
        return None
    return d


def _parse_int(raw: Any) -> int | None:
    if raw in (None, "", "null"):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_date(raw: Any) -> date | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.fromisoformat(str(raw).strip()).date()
    except ValueError:
        return None


def _own_property_or_404(property_id: int):
    """Load Property by id; 404 if not the current user's."""
    from .models import Property
    user_id = _current_user_id()
    if user_id is None:
        abort(401)
    p = Property.query.get(property_id)
    if p is None or p.user_id != user_id:
        abort(404)
    return p


# ---------------------------------------------------------------------------
# GET /property
# ---------------------------------------------------------------------------
@property_bp.route("", methods=["GET"])
@property_bp.route("/", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def index():
    """List the current user's properties + 'add new' card."""
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import Property
    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    rows = (
        Property.query
        .filter_by(user_id=user_id)
        .order_by(Property.created_at.desc())
        .all()
    )
    if request.args.get("format") == "json":
        return jsonify({"ok": True, "properties": [r.to_dict() for r in rows]})

    return render_template("property/index.html", properties=rows)


# ---------------------------------------------------------------------------
# POST /property
# ---------------------------------------------------------------------------
@property_bp.route("", methods=["POST"])
@property_bp.route("/", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def create():
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import Property, PROPERTY_TYPES, PURPOSES, CUSTOMER_STATUSES

    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    payload = request.get_json(silent=True) or request.form.to_dict()

    address_line1 = (payload.get("address_line1") or "").strip()
    city = (payload.get("city") or "").strip()
    if not address_line1 or not city:
        return jsonify({"ok": False, "error": "address_line1 and city are required"}), 400

    property_type = (payload.get("property_type") or "apartment").strip().lower()
    if property_type not in PROPERTY_TYPES:
        property_type = "other"
    purpose = (payload.get("purpose") or "mixed").strip().lower()
    if purpose not in PURPOSES:
        purpose = "mixed"
    customer_status = (payload.get("customer_status") or "tenant").strip().lower()
    if customer_status not in CUSTOMER_STATUSES:
        customer_status = "tenant"

    total_sqft = _parse_int(payload.get("total_sqft"))
    home_office_sqft = _parse_int(payload.get("home_office_sqft"))

    if total_sqft is not None and total_sqft < 0:
        return jsonify({"ok": False, "error": "total_sqft must be >= 0"}), 400
    if home_office_sqft is not None and home_office_sqft < 0:
        return jsonify({"ok": False, "error": "home_office_sqft must be >= 0"}), 400
    if (
        total_sqft is not None
        and home_office_sqft is not None
        and home_office_sqft > total_sqft
    ):
        return (
            jsonify({"ok": False, "error": "home_office_sqft cannot exceed total_sqft"}),
            400,
        )

    try:
        prop = Property(
            user_id=user_id,
            address_line1=address_line1,
            address_line2=(payload.get("address_line2") or "").strip() or None,
            city=city,
            postcode=(payload.get("postcode") or "").strip() or None,
            property_type=property_type,
            purpose=purpose,
            customer_status=customer_status,
            total_sqft=total_sqft,
            home_office_sqft=home_office_sqft,
        )
        prop.recompute_home_office_percentage()
        db.session.add(prop)
        db.session.commit()
        _invalidate_rental_cache_for(_current_user_id(), prop.id)
        # F-Platform-5: bust the savings cache + flag X-Fiesta-Event so the
        # topbar counter refreshes on the caller's next page load.
        try:
            from app import invalidate_savings_projection, fiesta_event_response
            invalidate_savings_projection(user_id)
            resp = jsonify({"ok": True, "property": prop.to_dict()})
            return fiesta_event_response(resp, 'property-added')
        except Exception:
            return jsonify({"ok": True, "property": prop.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Property create failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /property/<id>
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def detail(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import Landlord, RentalAgreement, LandlordRelationshipDetection
    prop = _own_property_or_404(property_id)
    landlord = Landlord.query.filter_by(property_id=property_id).first()
    rental = (
        RentalAgreement.query
        .filter_by(property_id=property_id)
        .order_by(RentalAgreement.start_date.desc())
        .first()
    )
    # D2 — pass landlord §195 detection so the template can surface it.
    landlord_detection = None
    landlord_reasoning: list = []
    if landlord is not None:
        landlord_detection = (
            LandlordRelationshipDetection.query
            .filter_by(landlord_id=landlord.id)
            .order_by(LandlordRelationshipDetection.detected_at.desc())
            .first()
        )
        if landlord_detection is not None:
            import json as _json
            try:
                landlord_reasoning = _json.loads(
                    landlord_detection.reasoning_json or "[]"
                )
            except Exception:
                landlord_reasoning = []

    if request.args.get("format") == "json":
        return jsonify({
            "ok": True,
            "property": prop.to_dict(),
            "landlord": landlord.to_dict() if landlord else None,
            "rental": rental.to_dict() if rental else None,
        })
    return render_template(
        "property/detail.html",
        property=prop,
        landlord=landlord,
        rental=rental,
        landlord_detection=landlord_detection,
        landlord_reasoning=landlord_reasoning,
    )


# ---------------------------------------------------------------------------
# PUT /property/<id>
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>", methods=["PUT", "POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def update(property_id: int):
    """Edit property. POST supported because HTML forms can't PUT natively."""
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import PROPERTY_TYPES, PURPOSES, CUSTOMER_STATUSES
    prop = _own_property_or_404(property_id)

    payload = request.get_json(silent=True) or request.form.to_dict()

    if "address_line1" in payload:
        v = (payload["address_line1"] or "").strip()
        if v:
            prop.address_line1 = v
    if "address_line2" in payload:
        prop.address_line2 = (payload["address_line2"] or "").strip() or None
    if "city" in payload:
        v = (payload["city"] or "").strip()
        if v:
            prop.city = v
    if "postcode" in payload:
        prop.postcode = (payload["postcode"] or "").strip() or None
    if "property_type" in payload:
        v = (payload["property_type"] or "").strip().lower()
        if v in PROPERTY_TYPES:
            prop.property_type = v
    if "purpose" in payload:
        v = (payload["purpose"] or "").strip().lower()
        if v in PURPOSES:
            prop.purpose = v
    if "customer_status" in payload:
        v = (payload["customer_status"] or "").strip().lower()
        if v in CUSTOMER_STATUSES:
            prop.customer_status = v

    if "total_sqft" in payload:
        v = _parse_int(payload["total_sqft"])
        if v is not None and v < 0:
            return jsonify({"ok": False, "error": "total_sqft must be >= 0"}), 400
        prop.total_sqft = v
    if "home_office_sqft" in payload:
        v = _parse_int(payload["home_office_sqft"])
        if v is not None and v < 0:
            return jsonify({"ok": False, "error": "home_office_sqft must be >= 0"}), 400
        prop.home_office_sqft = v

    if (
        prop.total_sqft is not None
        and prop.home_office_sqft is not None
        and prop.home_office_sqft > prop.total_sqft
    ):
        return (
            jsonify({"ok": False, "error": "home_office_sqft cannot exceed total_sqft"}),
            400,
        )

    prop.recompute_home_office_percentage()

    try:
        db.session.commit()
        _invalidate_rental_cache_for(_current_user_id(), prop.id)
        return jsonify({"ok": True, "property": prop.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Property update failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /property/<id>/landlord
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/landlord", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def landlord_detail(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import Landlord, LandlordRelationshipDetection
    prop = _own_property_or_404(property_id)
    landlord = Landlord.query.filter_by(property_id=property_id).first()
    detection = None
    if landlord is not None:
        detection = (
            LandlordRelationshipDetection.query
            .filter_by(landlord_id=landlord.id)
            .order_by(LandlordRelationshipDetection.detected_at.desc())
            .first()
        )

    if request.args.get("format") == "json":
        return jsonify({
            "ok": True,
            "property": prop.to_dict(),
            "landlord": landlord.to_dict() if landlord else None,
            "detection": detection.to_dict() if detection else None,
        })
    return render_template(
        "property/landlord.html",
        property=prop,
        landlord=landlord,
        detection=detection,
    )


# ---------------------------------------------------------------------------
# POST /property/<id>/landlord — add/edit landlord (triggers §195)
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/landlord", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def landlord_save(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import (
        Landlord, RentalAgreement, LandlordRelationshipDetection,
        RELATIONSHIPS,
    )

    user_id = _current_user_id()
    prop = _own_property_or_404(property_id)

    payload = request.get_json(silent=True) or request.form.to_dict()

    full_name = (payload.get("full_name") or "").strip()
    if not full_name:
        return jsonify({"ok": False, "error": "full_name is required"}), 400

    rel = (payload.get("relationship_to_customer") or "arm's-length").strip().lower()
    if rel not in RELATIONSHIPS:
        rel = "arm's-length"

    landlord = Landlord.query.filter_by(property_id=property_id).first()
    try:
        if landlord is None:
            landlord = Landlord(
                user_id=user_id,
                property_id=property_id,
                full_name=full_name,
                nic=(payload.get("nic") or None) or None,
                tin=(payload.get("tin") or None) or None,
                address=(payload.get("address") or None) or None,
                phone=(payload.get("phone") or None) or None,
                email=(payload.get("email") or None) or None,
                bank_name=(payload.get("bank_name") or None) or None,
                bank_account_number=(payload.get("bank_account_number") or None) or None,
                relationship_to_customer=rel,
            )
            db.session.add(landlord)
        else:
            landlord.full_name = full_name
            landlord.nic = (payload.get("nic") or None) or landlord.nic
            landlord.tin = (payload.get("tin") or None) or landlord.tin
            if "address" in payload:
                landlord.address = (payload.get("address") or None) or None
            if "phone" in payload:
                landlord.phone = (payload.get("phone") or None) or None
            if "email" in payload:
                landlord.email = (payload.get("email") or None) or None
            if "bank_name" in payload:
                landlord.bank_name = (payload.get("bank_name") or None) or None
            if "bank_account_number" in payload:
                landlord.bank_account_number = (
                    payload.get("bank_account_number") or None
                ) or None
            landlord.relationship_to_customer = rel
            landlord.updated_at = datetime.utcnow()

        db.session.flush()  # need landlord.id before persisting detection

        # §195 detection
        rental = (
            RentalAgreement.query
            .filter_by(property_id=property_id)
            .order_by(RentalAgreement.start_date.desc())
            .first()
        )
        rental_dict = rental.to_dict() if rental else None
        customer = _current_user_profile()
        detection = rp.detect_landlord_relationship(
            customer_profile=customer,
            landlord_record=landlord.to_dict(),
            property_record=prop.to_dict(),
            rental_agreement=rental_dict,
        )

        # Persist snapshot
        det_kwargs = rp.snapshot_to_persisted_fields(detection)
        det = LandlordRelationshipDetection(
            user_id=user_id,
            landlord_id=landlord.id,
            property_id=property_id,
            **det_kwargs,
        )
        db.session.add(det)

        db.session.commit()
        _invalidate_rental_cache_for(user_id, property_id)
        return jsonify({
            "ok": True,
            "landlord": landlord.to_dict(),
            "detection": detection,
        })
    except Exception as exc:
        db.session.rollback()
        logger.exception("Landlord save failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /property/<id>/rental
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/rental", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def rental_detail(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import Landlord, RentalAgreement
    prop = _own_property_or_404(property_id)
    landlord = Landlord.query.filter_by(property_id=property_id).first()
    rental = (
        RentalAgreement.query
        .filter_by(property_id=property_id)
        .order_by(RentalAgreement.start_date.desc())
        .first()
    )

    if request.args.get("format") == "json":
        return jsonify({
            "ok": True,
            "property": prop.to_dict(),
            "landlord": landlord.to_dict() if landlord else None,
            "rental": rental.to_dict() if rental else None,
        })
    return render_template(
        "property/rental.html",
        property=prop,
        landlord=landlord,
        rental=rental,
    )


# ---------------------------------------------------------------------------
# POST /property/<id>/rental — add/edit rental agreement
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/rental", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def rental_save(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import (
        Landlord, RentalAgreement, PAYMENT_METHODS, PAYMENT_FREQUENCIES,
        DEFAULT_AGREEMENT_DAYS,
    )

    user_id = _current_user_id()
    prop = _own_property_or_404(property_id)

    payload = request.get_json(silent=True) or request.form.to_dict()

    landlord = Landlord.query.filter_by(property_id=property_id).first()
    if landlord is None:
        # D4 — friendlier guard: redirect to landlord form with clear message
        # instead of returning a bare 400.
        if request.is_json or request.headers.get("Accept", "") == "application/json":
            return (
                jsonify({
                    "ok": False,
                    "error": "Add the landlord before the rental agreement",
                    "redirect_to": url_for(
                        "fiesta_property.landlord_detail",
                        property_id=property_id,
                    ),
                }),
                422,
            )
        flash(
            "You need to add a landlord first before creating a rental agreement. "
            "Fill in the landlord details below, then return to add the rental.",
            "warning",
        )
        return redirect(
            url_for("fiesta_property.landlord_detail", property_id=property_id)
        )

    start_date = _parse_date(payload.get("start_date")) or date.today()
    end_date = _parse_date(payload.get("end_date"))
    if end_date is None:
        end_date = start_date + timedelta(days=DEFAULT_AGREEMENT_DAYS)
    if end_date < start_date:
        return (
            jsonify({"ok": False, "error": "end_date cannot be before start_date"}),
            400,
        )

    monthly = _parse_decimal(payload.get("monthly_rent_lkr"))
    if monthly is None:
        monthly = Decimal("0")
    if monthly < 0:
        return jsonify({"ok": False, "error": "monthly_rent_lkr must be >= 0"}), 400

    deposit = _parse_decimal(payload.get("deposit_paid"))
    if deposit is not None and deposit < 0:
        return jsonify({"ok": False, "error": "deposit_paid must be >= 0"}), 400

    payment_method = (payload.get("payment_method") or "transfer").strip().lower()
    if payment_method not in PAYMENT_METHODS:
        payment_method = "transfer"
    payment_frequency = (payload.get("payment_frequency") or "monthly").strip().lower()
    if payment_frequency not in PAYMENT_FREQUENCIES:
        payment_frequency = "monthly"

    tax_year = (payload.get("tax_year") or _resolve_tax_year()).strip()

    rental = (
        RentalAgreement.query
        .filter_by(property_id=property_id, tax_year=tax_year)
        .first()
    )
    try:
        if rental is None:
            rental = RentalAgreement(
                user_id=user_id,
                property_id=property_id,
                landlord_id=landlord.id,
                start_date=start_date,
                end_date=end_date,
                payment_method=payment_method,
                payment_frequency=payment_frequency,
                tax_year=tax_year,
            )
            rental.monthly_rent_lkr = monthly
            if deposit is not None:
                rental.deposit_paid = deposit
            db.session.add(rental)
        else:
            rental.start_date = start_date
            rental.end_date = end_date
            rental.monthly_rent_lkr = monthly
            if deposit is not None:
                rental.deposit_paid = deposit
            rental.landlord_id = landlord.id
            rental.payment_method = payment_method
            rental.payment_frequency = payment_frequency
            rental.tax_year = tax_year
            rental.updated_at = datetime.utcnow()

        rental.apply_defaults(prop)
        db.session.commit()
        _invalidate_rental_cache_for(user_id, property_id)
        return jsonify({"ok": True, "rental": rental.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Rental save failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET /property/<id>/rental/preview — pre-S9 preview
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/rental/preview", methods=["GET"])
@login_required
def rental_preview(property_id: int):
    """302 redirect to the canonical rental-agreement path on fiesta_agreements_rental.

    B3 (F5.4): fiesta.agreements.rental_routes is the authoritative PDF-producing
    path.  This legacy route (GET /property/<id>/rental/preview) is retained only
    so that any bookmarked or cached URLs continue to work; it unconditionally
    redirects to /agreements/rental/<property_id>, forwarding the query string.
    """
    qs = request.query_string.decode("utf-8")
    target = url_for("fiesta_agreements_rental.preview", property_id=property_id)
    if qs:
        target = f"{target}?{qs}"
    return redirect(target, code=302)


# ---------------------------------------------------------------------------
# GET /property/<id>/sanity-check
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/sanity-check", methods=["GET"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def sanity_check(property_id: int):
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import RentalAgreement
    prop = _own_property_or_404(property_id)
    rental = (
        RentalAgreement.query
        .filter_by(property_id=property_id)
        .order_by(RentalAgreement.start_date.desc())
        .first()
    )

    monthly_rent_lkr = (
        float(rental.monthly_rent_lkr)
        if rental and rental.monthly_rent_lkr is not None
        else None
    )
    report = run_sanity_checks(
        monthly_rent_lkr=monthly_rent_lkr,
        total_sqft=prop.total_sqft,
        city=prop.city,
        home_office_percentage=prop.home_office_percentage,
    )

    return jsonify({
        "ok": True,
        "warnings": [
            {
                "code": w.code,
                "severity": w.severity,
                "message": w.message,
                "citation": w.citation,
            }
            for w in report.warnings
        ],
        "has_warnings": report.has_warnings,
    })


# ---------------------------------------------------------------------------
# POST /property/<id>/prefill-prior-year
# ---------------------------------------------------------------------------
@property_bp.route("/<int:property_id>/prefill-prior-year", methods=["POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def prefill_prior_year(property_id: int):
    """Copy prior year's RentalAgreement → current year (advances start_date)."""
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import RentalAgreement, DEFAULT_AGREEMENT_DAYS
    prop = _own_property_or_404(property_id)
    user_id = _current_user_id()

    current_year = (
        (request.get_json(silent=True) or request.form.to_dict() or {}).get("tax_year")
        or _resolve_tax_year()
    )

    # Pick most recent agreement other than current_year
    prior = (
        RentalAgreement.query
        .filter(
            RentalAgreement.property_id == property_id,
            RentalAgreement.tax_year != current_year,
        )
        .order_by(RentalAgreement.start_date.desc())
        .first()
    )
    if prior is None:
        return jsonify({
            "ok": False,
            "error": "No prior-year rental agreement found to prefill from",
        }), 404

    existing = (
        RentalAgreement.query
        .filter_by(property_id=property_id, tax_year=current_year)
        .first()
    )
    if existing is not None:
        return jsonify({
            "ok": False,
            "error": f"Agreement for {current_year} already exists; edit it directly",
        }), 409

    new_start = (prior.start_date or date.today()) + timedelta(days=365)
    new_end = new_start + timedelta(days=DEFAULT_AGREEMENT_DAYS)

    try:
        new_rental = RentalAgreement(
            user_id=user_id,
            property_id=property_id,
            landlord_id=prior.landlord_id,
            start_date=new_start,
            end_date=new_end,
            monthly_rent_lkr_cents=prior.monthly_rent_lkr_cents,
            deposit_paid_cents=prior.deposit_paid_cents,
            payment_method=prior.payment_method,
            payment_frequency=prior.payment_frequency,
            tax_year=current_year,
        )
        new_rental.apply_defaults(prop)
        db.session.add(new_rental)
        db.session.commit()
        return jsonify({"ok": True, "rental": new_rental.to_dict()})
    except Exception as exc:
        db.session.rollback()
        logger.exception("Prefill from prior year failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# GET/POST /property/setup — D3 consolidated single-page form (F4.8)
# ---------------------------------------------------------------------------
@property_bp.route("/setup", methods=["GET", "POST"])
@login_required
# LAUNCH 2026-05-26 (decision 1) - paywall off for data-recording.
def setup():
    """Consolidated Property + Landlord + RentalAgreement form (one click, three models).

    GET  — render templates/property/setup.html with empty (or re-populated) form.
    POST — parse namespaced fields, create Property → Landlord → RentalAgreement
           in a single DB transaction. On success redirect to /property/<id>.
           On validation error flash the specific problem and re-render the form
           with the submitted values restored.

    Field namespace:
      property_*  → Property model
      landlord_*  → Landlord model
      rental_*    → RentalAgreement model

    Coexists with the existing multi-hop flow (/property, /property/<id>/landlord,
    /property/<id>/rental). DO NOT modify those routes.
    """
    if not _HAS_DB:
        return jsonify({"ok": False, "error": "DB unavailable"}), 503

    from .models import (
        Property, Landlord, RentalAgreement,
        PROPERTY_TYPES, PURPOSES, CUSTOMER_STATUSES,
        RELATIONSHIPS, PAYMENT_METHODS, PAYMENT_FREQUENCIES,
        DEFAULT_AGREEMENT_DAYS,
    )

    user_id = _current_user_id()
    if not user_id:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    if request.method == "GET":
        return render_template("property/setup.html", form_data=None)

    # ── POST: parse consolidated form ────────────────────────────────────
    fd = request.form.to_dict()

    def _re_render(error_msg: str):
        flash(error_msg, "error")
        return render_template("property/setup.html", form_data=fd), 400

    # ── Property fields ──────────────────────────────────────────────────
    address_line1 = (fd.get("property_address_line1") or "").strip()
    city = (fd.get("property_city") or "").strip()
    if not address_line1:
        return _re_render("Address line 1 is required.")
    if not city:
        return _re_render("City is required.")

    property_type = (fd.get("property_type") or "apartment").strip().lower()
    if property_type not in PROPERTY_TYPES:
        property_type = "other"
    purpose = (fd.get("property_purpose") or "mixed").strip().lower()
    if purpose not in PURPOSES:
        purpose = "mixed"
    customer_status = (fd.get("property_customer_status") or "tenant").strip().lower()
    if customer_status not in CUSTOMER_STATUSES:
        customer_status = "tenant"

    total_sqft = _parse_int(fd.get("property_total_sqft"))
    home_office_sqft = _parse_int(fd.get("property_home_office_sqft"))
    if total_sqft is not None and total_sqft < 0:
        return _re_render("Total floor area must be 0 or greater.")
    if home_office_sqft is not None and home_office_sqft < 0:
        return _re_render("Home-office area must be 0 or greater.")
    if (
        total_sqft is not None
        and home_office_sqft is not None
        and home_office_sqft > total_sqft
    ):
        return _re_render("Home-office area cannot exceed total floor area.")

    # ── Landlord fields ──────────────────────────────────────────────────
    landlord_full_name = (fd.get("landlord_full_name") or "").strip()
    if not landlord_full_name:
        return _re_render("Landlord's full name is required.")

    rel = (fd.get("landlord_relationship_to_customer") or "arm's-length").strip().lower()
    if rel not in RELATIONSHIPS:
        rel = "arm's-length"

    # ── Rental fields ────────────────────────────────────────────────────
    monthly_rent = _parse_decimal(fd.get("rental_monthly_rent_lkr"))
    if monthly_rent is None:
        return _re_render("Monthly rent (LKR) is required.")
    if monthly_rent < 0:
        return _re_render("Monthly rent must be 0 or greater.")

    deposit = _parse_decimal(fd.get("rental_deposit_paid"))
    if deposit is not None and deposit < 0:
        return _re_render("Security deposit must be 0 or greater.")

    start_date = _parse_date(fd.get("rental_start_date")) or date.today()
    end_date = _parse_date(fd.get("rental_end_date"))
    if end_date is None:
        end_date = start_date + timedelta(days=DEFAULT_AGREEMENT_DAYS)
    if end_date < start_date:
        return _re_render("Agreement end date cannot be before the start date.")

    payment_method = (fd.get("rental_payment_method") or "transfer").strip().lower()
    if payment_method not in PAYMENT_METHODS:
        payment_method = "transfer"
    payment_frequency = (fd.get("rental_payment_frequency") or "monthly").strip().lower()
    if payment_frequency not in PAYMENT_FREQUENCIES:
        payment_frequency = "monthly"

    tax_year = (fd.get("rental_tax_year") or _resolve_tax_year()).strip()

    # ── Single DB transaction: Property → Landlord → RentalAgreement ────
    try:
        # 1. Property
        prop = Property(
            user_id=user_id,
            address_line1=address_line1,
            address_line2=(fd.get("property_address_line2") or "").strip() or None,
            city=city,
            postcode=(fd.get("property_postcode") or "").strip() or None,
            property_type=property_type,
            purpose=purpose,
            customer_status=customer_status,
            total_sqft=total_sqft,
            home_office_sqft=home_office_sqft,
        )
        prop.recompute_home_office_percentage()
        db.session.add(prop)
        db.session.flush()  # assign prop.id before Landlord FK

        # 2. Landlord (FK → prop)
        landlord = Landlord(
            user_id=user_id,
            property_id=prop.id,
            full_name=landlord_full_name,
            nic=(fd.get("landlord_nic") or None) or None,
            tin=(fd.get("landlord_tin") or None) or None,
            address=(fd.get("landlord_address") or None) or None,
            phone=(fd.get("landlord_phone") or None) or None,
            email=(fd.get("landlord_email") or None) or None,
            bank_name=(fd.get("landlord_bank_name") or None) or None,
            bank_account_number=(fd.get("landlord_bank_account_number") or None) or None,
            relationship_to_customer=rel,
        )
        db.session.add(landlord)
        db.session.flush()  # assign landlord.id before RentalAgreement FK

        # 3. RentalAgreement (FK → prop + landlord)
        rental = RentalAgreement(
            user_id=user_id,
            property_id=prop.id,
            landlord_id=landlord.id,
            start_date=start_date,
            end_date=end_date,
            payment_method=payment_method,
            payment_frequency=payment_frequency,
            tax_year=tax_year,
        )
        rental.monthly_rent_lkr = monthly_rent
        if deposit is not None:
            rental.deposit_paid = deposit
        rental.apply_defaults(prop)  # stamp-duty end-date + home-office portion
        db.session.add(rental)

        db.session.commit()

        # F-Platform-5: bust the savings cache + queue the property-added
        # event so the topbar counter refreshes on the redirected /property/<id>.
        try:
            from app import invalidate_savings_projection, queue_fiesta_event
            invalidate_savings_projection(user_id)
            queue_fiesta_event('property-added')
        except Exception:
            pass

        flash(
            f"Property, landlord, and rental agreement saved for {address_line1}, {city}.",
            "success",
        )
        return redirect(url_for("fiesta_property.detail", property_id=prop.id))

    except Exception as exc:
        db.session.rollback()
        logger.exception("Consolidated property setup failed")
        return _re_render(f"Save failed — please try again. ({exc})")


# ---------------------------------------------------------------------------
# Registration helper (called from main.py)
# ---------------------------------------------------------------------------
def register_blueprint(app):
    """Register the property blueprint with the Flask app."""
    app.register_blueprint(property_bp)
    logger.info("FIESTA S7 property blueprint registered at /property")
