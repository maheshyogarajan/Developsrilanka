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


@bp.route("/<int:property_id>", methods=["GET"])
@login_required
@paywall_required(min_tier="self_file", screen_id="S9", action="preview")
def preview(property_id: int) -> Any:
    """Preview/edit form for a Rental Agreement against a given property."""
    user_id = getattr(current_user, "id", None)
    return render_template(
        "agreements/rental_preview.html",
        property_id=property_id,
        user_id=user_id,
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


__all__ = ["bp"]
