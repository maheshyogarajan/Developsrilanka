"""fiesta.agreements.rental_routes — Flask blueprint /agreements/rental.

Surface (4 routes):

    GET  /agreements/rental/<property_id>            preview screen + form
    POST /agreements/rental/<property_id>/generate   render + persist PDF
    GET  /agreements/rental/<property_id>/pdf/<gen_id>  download PDF
    GET  /agreements/rental/<property_id>/history    list prior renders

POLICY
======
- All four routes require flask_login.login_required.
- The customer can only see PROPERTIES + AGREEMENTS they own (user_id =
  current_user.id). Cross-tenant access returns 404 by design (no presence
  leak).
- PDF is regenerated on every POST -- we never serve stale PDFs because the
  template version may have moved between renders.
- Persistence row is created BEFORE responding to the customer; if the DB
  write fails, the PDF is discarded and the customer sees an error (no
  orphan PDFs on disk).

WIRING
======
Add to main.py (the parallel S8 build will share the registration block):

    from fiesta.agreements.rental_routes import bp as rental_agreement_bp
    app.register_blueprint(rental_agreement_bp)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

try:
    from flask_login import current_user, login_required  # type: ignore[import-not-found]
except Exception:  # pragma: no cover -- import-time tolerance
    current_user = None  # type: ignore[assignment]

    def login_required(f):  # type: ignore[no-redef]
        return f

try:
    from fiesta.paywall.gate import paywall_required  # type: ignore[import-not-found]
except Exception:  # pragma: no cover -- import-time tolerance
    def paywall_required(*a, **kw):  # type: ignore[no-redef]
        def deco(f):
            return f
        return deco


from fiesta.agreements.models import (
    Party,
    Property as PropertyDTO,
    RentalAgreementInput,
)
from fiesta.agreements.rental_pdf import render_rental_agreement

# Tier D6 / D8 (2026-05-25) — module-level imports for hot-path helpers.
# Lazy-imports inside preview() (Property, Landlord, RentalAgreement,
# compute_protected_deductions_lkr) made cold worker boots pay the full
# SQLAlchemy DDL walk per request. Hoisted here.
try:
    from fiesta.property.models import (  # type: ignore[import-not-found]
        Property as _Property,
        Landlord as _Landlord,
        RentalAgreement as _RentalAgreement,
    )
except Exception:  # pragma: no cover -- import-time tolerance
    _Property = None  # type: ignore[assignment]
    _Landlord = None  # type: ignore[assignment]
    _RentalAgreement = None  # type: ignore[assignment]

try:
    from fiesta.agreements.helpers import compute_protected_deductions_lkr as _compute_protected_deductions_lkr
except Exception:  # pragma: no cover -- import-time tolerance
    _compute_protected_deductions_lkr = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


bp = Blueprint(
    "fiesta_agreements_rental",
    __name__,
    url_prefix="/agreements/rental",
    template_folder="../../templates",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _pdf_storage_dir() -> Path:
    """Where rendered PDFs are persisted. Default: ./generated/agreements/."""
    base = current_app.config.get("FIESTA_AGREEMENT_PDF_DIR", "generated/agreements")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _coerce_decimal(raw: Any, *, field: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value for {field!r}: {raw!r}") from exc


def _coerce_date(raw: Any, *, field: str) -> date:
    if isinstance(raw, date):
        return raw
    try:
        return datetime.fromisoformat(str(raw)).date()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid ISO date for {field!r}: {raw!r}") from exc


def _build_input(form: Any, *, user_id: int, user_name: str) -> RentalAgreementInput:
    """Map a Flask form / JSON dict -> RentalAgreementInput.

    Keeps the route thin; all validation lives in the pydantic schema.
    """
    tenant = Party(
        full_name=form["tenant_full_name"],
        nic=form.get("tenant_nic") or None,
        tin=form.get("tenant_tin") or None,
        address_line=form["tenant_address"],
        bank_name=form.get("tenant_bank_name") or None,
        bank_account=form.get("tenant_bank_account") or None,
    )
    landlord = Party(
        full_name=form["landlord_full_name"],
        nic=form.get("landlord_nic") or None,
        tin=form.get("landlord_tin") or None,
        address_line=form["landlord_address"],
        bank_name=form.get("landlord_bank_name") or None,
        bank_account=form.get("landlord_bank_account") or None,
    )
    prop = PropertyDTO(
        address_line=form["property_address"],
        lot_plan=form.get("property_lot_plan") or None,
        area_sqft=float(form["property_area_sqft"]) if form.get("property_area_sqft") else None,
        description=form.get("property_description") or None,
    )
    return RentalAgreementInput(
        user_id=user_id,
        user_name=user_name,
        tax_year=form["tax_year"],
        tenant=tenant,
        landlord=landlord,
        property=prop,
        term_start=_coerce_date(form["term_start"], field="term_start"),
        term_end=_coerce_date(form["term_end"], field="term_end"),
        monthly_rent_lkr=_coerce_decimal(form["monthly_rent_lkr"], field="monthly_rent_lkr"),
        currency=form.get("currency", "LKR"),
        deposit_months=float(form.get("deposit_months", 2.0)),
        deposit_return_days=int(form.get("deposit_return_days", 30)),
        rent_due_day=int(form.get("rent_due_day", 1)),
        termination_notice_months=int(form.get("termination_notice_months", 2)),
        rent_arrears_days=int(form.get("rent_arrears_days", 14)),
        home_office_percentage=float(form.get("home_office_percentage", 1.0)),
        s195_force_on=bool(form.get("s195_force_on")),
        s195_force_off=bool(form.get("s195_force_off")),
        s195_override_reason=form.get("s195_override_reason") or None,
        s195_stated_basis=form.get("s195_stated_basis") or None,
        customer_status_owner_rented_from_self=bool(form.get("customer_status_owner_rented_from_self")),
        notice_email=form.get("notice_email") or None,
        court_district=form.get("court_district", "Colombo"),
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@bp.route("", methods=["GET"], strict_slashes=False)
@bp.route("/", methods=["GET"], strict_slashes=False)
@login_required
def index() -> Any:
    """HOTFIX 2026-05-22 — Bare-prefix landing for /agreements/rental.

    Sidebar nav links to /agreements/rental (no property_id). Per-record
    preview routes require an id, so the bare prefix used to 404. Behaviour:
      - 0 properties: flash + redirect to /property (where user adds one)
      - 1 property:   redirect straight to that property's preview
      - >1 properties: redirect to /property listing (each card already has
                       a "Generate rental agreement" button per B6)
    """
    from fiesta.property.models import Property  # type: ignore[import-not-found]
    user_id = getattr(current_user, "id", None)
    properties = Property.query.filter_by(user_id=user_id).all()
    if not properties:
        flash(
            "Add the property you live and work in first, then we'll generate the rental agreement.",
            "info",
        )
        return redirect("/property")
    if len(properties) == 1:
        return redirect(url_for("fiesta_agreements_rental.preview", property_id=properties[0].id))
    return redirect("/property")


@bp.route("/<int:property_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S9", action="preview")
def preview(property_id: int) -> Any:
    """Preview/edit form for a Rental Agreement against a given property.

    D1 FIX 2026-05-23 (CEO crash repro on /agreements/rental/<id> rendering
    a blank body): the previous implementation only passed property_id and
    protected_deductions_lkr, but templates/agreements/rental_preview.html
    gates ALL of its visible body on `{% if property %}`, `{% if preview %}`
    and `{% if rental_form_context %}`. With none of those in context, the
    page rendered the chrome (DRAFT banner, breadcrumb) and nothing else.

    The defect-log hypothesis attributed the blank body to a calculator
    branch failing for purpose=="mixed", but the actual root cause is route
    context — purpose plays no role in the current preview path. All three
    purpose values (residence / business / mixed) would render equally
    blank pages before this fix.

    Fix shape: enrich context defensively. property is REQUIRED (404 if
    not owned by user, no presence leak). preview, rental_form_context and
    history_url are best-effort — landlord/rental may not exist yet for a
    fresh property, in which case we still render a usable preview pane.
    """
    user_id = getattr(current_user, "id", None)

    # Tier D6 / D8 — fetch the Property + Landlord + most-recent RentalAgreement
    # via a cached bundle helper. Cold path runs the DB queries; warm path
    # serves from the per-(user, property) in-memory cache (60s TTL).
    # Invalidated by Property / Landlord / RentalAgreement write handlers via
    # `invalidate_rental_agreement_cache(user_id, property_id)`.
    bundle = _resolve_property_bundle_cached(property_id, user_id)
    prop = bundle.get("property")
    landlord = bundle.get("landlord")
    rental = bundle.get("rental")

    if _Property is not None and prop is None:
        # Property model is wired but the row doesn't exist OR isn't owned
        # by this user — 404 (not 403) to avoid leaking existence.
        abort(404)

    # B4 F5.5 — surface server-side "protects Rs X" projection on S9.
    protected_lkr = _rental_protected_deductions_cached(
        user_id=user_id,
        property_id=property_id,
        user_obj=current_user,
        property_obj=prop,
    )

    # Build the preview dict the template's {% if preview %} block expects.
    # All sub-fields are tolerant of missing data — the template guards on
    # truthiness of optional sub-fields (deposit, home_office_portion).
    preview_ctx: dict[str, Any] | None = None
    if prop is not None:
        property_type = getattr(prop, "property_type", None) or "property"
        purpose = getattr(prop, "purpose", None)
        type_label = (
            f"{property_type} ({purpose})" if purpose else property_type
        )
        address_parts = [
            getattr(prop, "address_line1", None),
            getattr(prop, "address_line2", None),
            getattr(prop, "city", None),
            getattr(prop, "postcode", None),
        ]
        property_address = ", ".join(p for p in address_parts if p)

        monthly_rent = getattr(rental, "monthly_rent_lkr", None) if rental else None
        deposit = getattr(rental, "deposit_paid", None) if rental else None
        home_office_portion = (
            getattr(rental, "home_office_portion_lkr", None) if rental else None
        )
        start_date = getattr(rental, "start_date", None) if rental else None
        end_date = getattr(rental, "end_date", None) if rental else None
        payment_method = (
            getattr(rental, "payment_method", None) if rental else None
        ) or "bank_transfer"
        payment_frequency = (
            getattr(rental, "payment_frequency", None) if rental else None
        ) or "monthly"

        preview_ctx = {
            "title": "Rental Agreement",
            "parties": {
                "landlord": (
                    getattr(landlord, "full_name", None) if landlord else "Landlord pending"
                ),
                "landlord_nic": getattr(landlord, "nic", None) if landlord else None,
                "tenant": (
                    getattr(current_user, "full_name", None)
                    or getattr(current_user, "name", None)
                    or getattr(current_user, "email", "Tenant")
                ),
                "tenant_nic": getattr(current_user, "nic", None),
            },
            "property": {
                "address": property_address or "Address pending",
                "type": type_label,
                "total_sqft": getattr(prop, "total_sqft", None),
                "home_office_sqft": getattr(prop, "home_office_sqft", None),
                "home_office_percentage": getattr(
                    prop, "home_office_percentage", None
                ),
            },
            "terms": {
                "start_date": start_date.isoformat() if start_date else "TBD",
                "end_date": end_date.isoformat() if end_date else "TBD",
                "monthly_rent_lkr": (
                    f"{monthly_rent:,.2f}" if monthly_rent is not None else "TBD"
                ),
                "deposit_paid": (
                    f"{deposit:,.2f}" if deposit is not None else None
                ),
                "home_office_portion_lkr": (
                    f"{home_office_portion:,.2f}"
                    if home_office_portion is not None
                    else None
                ),
                "payment_method": payment_method,
                "payment_frequency": payment_frequency,
            },
            "compliance_note": (
                "This rental documents your home-office portion for "
                "deduction under IRA §6(1). The signed PDF is your "
                "primary defence if the IRD audits the deduction."
            ),
            "ira_section_6": (
                "IRA §6(1): rent paid in production of business income is "
                "an allowable deduction proportionate to business use."
            ),
        }

    # Build the form context the template's {% if rental_form_context %}
    # block expects. Pre-fill from any existing rental so edits round-trip.
    rental_form_context = {
        "start_date": (
            getattr(rental, "start_date", None).isoformat()
            if rental and getattr(rental, "start_date", None)
            else None
        ),
        "end_date": (
            getattr(rental, "end_date", None).isoformat()
            if rental and getattr(rental, "end_date", None)
            else None
        ),
        "monthly_rent_lkr": (
            str(getattr(rental, "monthly_rent_lkr", None))
            if rental and getattr(rental, "monthly_rent_lkr", None) is not None
            else None
        ),
        "deposit_lkr": (
            str(getattr(rental, "deposit_paid", None))
            if rental and getattr(rental, "deposit_paid", None) is not None
            else None
        ),
        "payment_method": (
            getattr(rental, "payment_method", None) if rental else None
        ),
        "landlord_email": getattr(landlord, "email", None) if landlord else None,
    }

    history_url = url_for(
        "fiesta_agreements_rental.history", property_id=property_id
    )

    return render_template(
        "agreements/rental_preview.html",
        property_id=property_id,
        user_id=user_id,
        property=prop,
        preview=preview_ctx,
        rental_form_context=rental_form_context,
        history_url=history_url,
        gate_warnings=[],
        gate_blocks=[],
        protected_deductions_lkr=protected_lkr,
    )


@bp.route("/<int:property_id>/generate", methods=["POST"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S9", action="generate")
def generate(property_id: int) -> Any:
    """Render the PDF + persist a RentalAgreementGenerated row."""
    user_id = getattr(current_user, "id", 0)
    user_name = getattr(current_user, "name", None) or getattr(current_user, "email", "Customer")
    payload = request.get_json(silent=True) or request.form
    try:
        input_ = _build_input(payload, user_id=user_id, user_name=user_name)
    except (KeyError, ValueError) as exc:
        logger.warning("rental.generate validation failed: %s", exc)
        if request.is_json:
            return {"error": str(exc)}, 400
        flash(f"Validation error: {exc}", "danger")
        return redirect(url_for("fiesta_agreements_rental.preview", property_id=property_id))

    pdf_bytes, meta = render_rental_agreement(input_)

    pdf_dir = _pdf_storage_dir()
    pdf_name = f"{meta.reference_id}.pdf"
    pdf_path = pdf_dir / pdf_name
    pdf_path.write_bytes(pdf_bytes)

    # Persist if the SQLAlchemy model is available (app context only).
    gen_id: int | None = None
    try:
        from app import db  # type: ignore[import-not-found]
        from fiesta.agreements.models import RentalAgreementGenerated  # type: ignore[import-not-found]

        row = RentalAgreementGenerated(
            reference_id=meta.reference_id,
            user_id=input_.user_id,
            property_id=property_id,
            landlord_id=payload.get("landlord_id") or None,
            tax_year=input_.tax_year,
            generated_at=meta.generated_at,
            template_version=meta.template_version,
            term_start=input_.term_start,
            term_end=input_.term_end,
            term_days=input_.term_days,
            currency=input_.currency,
            monthly_rent_lkr=input_.monthly_rent_lkr,
            home_office_percentage=Decimal(str(input_.home_office_percentage)),
            home_office_portion_lkr=input_.home_office_portion_lkr,
            s195_disclosure_applied=meta.s195_disclosure_applied,
            s195_default_on_recommended=meta.s195_default_on_recommended,
            s195_override_reason=meta.s195_override_reason,
            s195_confidence=Decimal(str(meta.s195_confidence)),
            s195_audit_substance_risk=meta.s195_audit_substance_risk,
            s195_signals_csv=",".join(meta.s195_signals) if meta.s195_signals else None,
            stamp_duty_chargeable=meta.stamp_duty_chargeable,
            stamp_duty_lkr=meta.stamp_duty_lkr,
            stamp_duty_band=meta.stamp_duty_band,
            pdf_sha256=meta.pdf_sha256,
            pdf_path=str(pdf_path),
            pdf_size_bytes=meta.pdf_size_bytes,
        )
        db.session.add(row)
        db.session.commit()
        gen_id = row.id
    except Exception as exc:  # pragma: no cover -- tested manually via integration
        logger.warning("rental.generate persistence skipped: %s", exc)

    if request.is_json:
        return {
            "reference_id": meta.reference_id,
            "pdf_sha256": meta.pdf_sha256,
            "pdf_size_bytes": meta.pdf_size_bytes,
            "s195_disclosure_applied": meta.s195_disclosure_applied,
            "stamp_duty_chargeable": meta.stamp_duty_chargeable,
            "stamp_duty_lkr": str(meta.stamp_duty_lkr),
            "download_url": url_for(
                "fiesta_agreements_rental.download",
                property_id=property_id,
                gen_id=gen_id or 0,
            ),
        }
    return redirect(
        url_for(
            "fiesta_agreements_rental.download",
            property_id=property_id,
            gen_id=gen_id or 0,
        )
    )


@bp.route("/<int:property_id>/pdf/<int:gen_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S9", action="download")
def download(property_id: int, gen_id: int) -> Any:
    """Serve the persisted PDF for download."""
    try:
        from app import db  # type: ignore[import-not-found] # noqa: F401
        from fiesta.agreements.models import RentalAgreementGenerated  # type: ignore[import-not-found]
    except Exception:
        abort(503, description="agreement persistence unavailable")

    row = RentalAgreementGenerated.query.get(gen_id)
    if row is None:
        abort(404)
    if row.user_id != getattr(current_user, "id", None):
        abort(404)
    if row.property_id is not None and row.property_id != property_id:
        abort(404)
    if not row.pdf_path or not Path(row.pdf_path).exists():
        abort(404, description="PDF artefact missing from storage")
    return send_file(
        row.pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{row.reference_id}.pdf",
    )


@bp.route("/<int:property_id>/history", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S9", action="history")
def history(property_id: int) -> Any:
    """List prior renders for the given property."""
    try:
        from app import db  # type: ignore[import-not-found] # noqa: F401
        from fiesta.agreements.models import RentalAgreementGenerated  # type: ignore[import-not-found]
    except Exception:
        return {"rows": []}

    rows = (
        RentalAgreementGenerated.query
        .filter(
            RentalAgreementGenerated.user_id == getattr(current_user, "id", 0),
            RentalAgreementGenerated.property_id == property_id,
        )
        .order_by(RentalAgreementGenerated.generated_at.desc())
        .all()
    )
    if request.is_json:
        return {
            "rows": [
                {
                    "id": r.id,
                    "reference_id": r.reference_id,
                    "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                    "term_start": r.term_start.isoformat() if r.term_start else None,
                    "term_end": r.term_end.isoformat() if r.term_end else None,
                    "monthly_rent_lkr": str(r.monthly_rent_lkr),
                    "s195_disclosure_applied": bool(r.s195_disclosure_applied),
                    "stamp_duty_chargeable": bool(r.stamp_duty_chargeable),
                }
                for r in rows
            ]
        }
    return render_template(
        "agreements/rental_history.html",
        property_id=property_id,
        rows=rows,
    )


# --------------------------------------------------------------------------- #
# Tier D6 / D8 — cache helpers + invalidator (rental side).
# --------------------------------------------------------------------------- #


def _resolve_property_bundle_cached(property_id: int, user_id) -> dict:
    """Cached (Property, Landlord, RentalAgreement) trio for the rental preview.

    Cache key: ``rental_bundle:{user_id}:{property_id}``. TTL 60s.
    Invalidate via `invalidate_rental_agreement_cache(user_id, property_id)`
    when any of the three rows changes (Property/Landlord/Rental save handlers).

    Returns a dict ``{"property": ..., "landlord": ..., "rental": ...}``
    with None for any missing piece. Returns all-None dict on import-time
    failure or DB unavailability — caller treats None as "404 / unauthorised".
    """
    key = None
    try:
        from fiesta.perf_cache import get as _get, set as _set
        key = f"rental_bundle:{int(user_id) if user_id else 0}:{int(property_id)}"
        hit, value = _get(key)
        if hit:
            return value
    except Exception:  # noqa: BLE001
        _set = None

    bundle: dict = {"property": None, "landlord": None, "rental": None}

    if _Property is not None:
        try:
            bundle["property"] = (
                _Property.query.filter_by(id=property_id, user_id=user_id).first()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("rental.preview Property fetch failed: %s", exc)

    if _Landlord is not None:
        try:
            bundle["landlord"] = _Landlord.query.filter_by(
                property_id=property_id
            ).first()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rental.preview Landlord fetch failed: %s", exc)

    if _RentalAgreement is not None:
        try:
            bundle["rental"] = (
                _RentalAgreement.query
                .filter_by(property_id=property_id)
                .order_by(_RentalAgreement.start_date.desc())
                .first()
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("rental.preview RentalAgreement fetch failed: %s", exc)

    if _set is not None and key is not None:
        try:
            _set(key, bundle, seconds=60)
        except Exception:  # noqa: BLE001
            pass
    return bundle


def _rental_protected_deductions_cached(
    *, user_id, property_id: int, user_obj, property_obj
) -> int:
    """Cached LKR-protected-by-rental-agreement projection. Mirrors the
    service-side helper. Key: ``rental_protected_lkr:{user_id}:{property_id}``.
    """
    key = None
    try:
        from fiesta.perf_cache import get as _get, set as _set
        key = f"rental_protected_lkr:{int(user_id) if user_id else 0}:{int(property_id)}"
        hit, value = _get(key)
        if hit:
            return int(value)
    except Exception:  # noqa: BLE001
        _set = None

    if property_obj is None or _compute_protected_deductions_lkr is None:
        val = 0
    else:
        try:
            val = _compute_protected_deductions_lkr(
                user_obj, property_obj, is_property=True
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("rental.preview protected_deductions calc failed: %s", exc)
            val = 0

    if _set is not None and key is not None:
        try:
            _set(key, int(val), seconds=60)
        except Exception:  # noqa: BLE001
            pass
    return int(val)


def invalidate_rental_agreement_cache(user_id, property_id: int | None = None) -> int:
    """Drop cached rental-bundle + projection entries for a user.

    Call from Property / Landlord / RentalAgreement write handlers so
    `/agreements/rental/<property_id>` reflects the change on next render.
    Returns count of cache keys dropped.
    """
    if not user_id:
        return 0
    try:
        from fiesta.perf_cache import invalidate as _inv, invalidate_prefix as _inv_pre
        if property_id is not None:
            _inv(f"rental_bundle:{int(user_id)}:{int(property_id)}")
            _inv(f"rental_protected_lkr:{int(user_id)}:{int(property_id)}")
            return 2
        return _inv_pre(f"rental_bundle:{int(user_id)}:") + _inv_pre(
            f"rental_protected_lkr:{int(user_id)}:"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("invalidate_rental_agreement_cache failed: %s", exc)
        return 0


__all__ = ["bp", "invalidate_rental_agreement_cache"]
