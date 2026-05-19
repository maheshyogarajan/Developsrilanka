"""fiesta.agreements.models -- DB models for generated Service Agreements (S8) and Rental Agreements (S9).

Wave 3 (2026-05-20). Per the S8 dispatch brief + G.1.3 v0.1 proposal.

Storage model (S8 ServiceAgreement)
-----------------------------------
- ServiceAgreement row per generation. NOT mutable -- regenerating creates a
  new row (different reference_id, different sha256). Audit trail = the whole
  table.
- PDF artefact stored either as a filesystem blob (pdf_path) OR an S3 key
  (pdf_s3_key); both columns nullable so deploys can pick. Hash (sha256)
  stored separately so the artefact's integrity can be re-verified at any
  later moment.
- §195 disclosure state is recorded as a triple:
    sec195_disclosure_applied  : bool (clause WAS rendered into the PDF)
    sec195_default_was_on      : bool (the detector said "default ON")
    sec195_override_reason     : optional text -- when customer marked the
                                 deal arm's-length, the justification text
                                 they typed. Note: the override does NOT
                                 suppress the disclosure clause in the PDF
                                 (clause still ships), but it is captured for
                                 audit defence.

S9 RentalAgreementGenerated
---------------------------
SQLAlchemy model + Pydantic v2 DTOs (RentalAgreementInput,
RentalAgreementGeneratedSchema). Append-only audit trail; re-renders produce
additional rows; we never update or delete.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# SQLAlchemy models -- guarded so importing this module does not require the
# Flask app to be initialised. Test suite can run without app context.
# --------------------------------------------------------------------------- #

try:
    from app import db  # type: ignore[import-not-found]
    _HAS_APP_DB = True
except Exception:  # pragma: no cover -- testing path
    db = None  # type: ignore[assignment]
    _HAS_APP_DB = False


if _HAS_APP_DB:  # pragma: no branch -- guarded import

    class ServiceAgreement(db.Model):  # type: ignore[misc]
        """One row per generated Service Agreement PDF (S8 Wave 3)."""

        __tablename__ = "service_agreements"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
        )
        # ServiceProvider is a future S6 model; until that lands, we accept the
        # opaque external id (string) so this table is forward-compatible without
        # depending on a class that may not exist yet on every branch.
        service_provider_id = db.Column(db.String(64), nullable=False, index=True)

        # Identity + provenance.
        reference_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
        template_version = db.Column(db.String(16), nullable=False, default="v0.1-draft")
        generated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        generated_by_ip = db.Column(db.String(64), nullable=True)

        # Customer + counterparty snapshot at generation time (JSON text -- the
        # PDF is the canonical artefact; this is for searchability + replay).
        customer_snapshot_json = db.Column(db.Text, nullable=False, default="{}")
        sp_snapshot_json = db.Column(db.Text, nullable=False, default="{}")
        parameters_snapshot_json = db.Column(db.Text, nullable=False, default="{}")

        # Agreement parameters.
        governing_law_variant = db.Column(db.String(1), nullable=False, default="A")
        fee_structure_variant = db.Column(db.String(1), nullable=False, default="A")
        ip_variant = db.Column(db.String(1), nullable=False, default="A")
        renewal_variant = db.Column(db.String(1), nullable=False, default="A")
        currency = db.Column(db.String(8), nullable=False, default="LKR")
        term_start = db.Column(db.Date, nullable=True)
        term_end = db.Column(db.Date, nullable=True)
        monthly_fee_lkr = db.Column(db.Numeric(14, 2), nullable=True)

        # PDF artefact.
        pdf_path = db.Column(db.String(512), nullable=True)
        pdf_s3_key = db.Column(db.String(512), nullable=True)
        pdf_sha256 = db.Column(db.String(64), nullable=False)
        pdf_byte_size = db.Column(db.Integer, nullable=False, default=0)

        # Signature state.
        customer_signature_status = db.Column(
            db.String(32), nullable=False, default="unsigned"
        )
        sp_signature_status = db.Column(
            db.String(32), nullable=False, default="unsigned"
        )
        customer_signed_at = db.Column(db.DateTime, nullable=True)
        sp_signed_at = db.Column(db.DateTime, nullable=True)

        # §195 disclosure audit trail.
        sec195_disclosure_applied = db.Column(db.Boolean, nullable=False, default=False)
        sec195_default_was_on = db.Column(db.Boolean, nullable=False, default=False)
        sec195_override_reason = db.Column(db.Text, nullable=True)
        sec195_confidence = db.Column(db.Float, nullable=True)
        sec195_signals_json = db.Column(db.Text, nullable=True)

        # X6 compliance gate snapshot at generation time.
        gate_passed = db.Column(db.Boolean, nullable=False, default=True)
        gate_warnings_count = db.Column(db.Integer, nullable=False, default=0)
        gate_blocks_count = db.Column(db.Integer, nullable=False, default=0)
        gate_trace_json = db.Column(db.Text, nullable=True)

        # Lifecycle.
        is_draft_preview = db.Column(db.Boolean, nullable=False, default=False)
        superseded_by_id = db.Column(
            db.Integer, db.ForeignKey("service_agreements.id"), nullable=True
        )

        def __repr__(self) -> str:  # pragma: no cover -- debug only
            return (
                f"<ServiceAgreement id={self.id} ref={self.reference_id} "
                f"user_id={self.user_id} sp={self.service_provider_id} "
                f"sec195={self.sec195_disclosure_applied}>"
            )


    class RentalAgreementGenerated(db.Model):  # type: ignore[misc]
        """One row per Rental Agreement PDF render (S9). Append-only audit trail.

        Re-renders of the same reference produce additional rows; we never
        update or delete. The PDF on disk (pdf_path) may be regenerated if
        the template version bumps -- the old row stays as evidence of what
        the customer presented to a third party at a given point in time.
        """

        __tablename__ = "rental_agreement_generated"

        id = db.Column(db.Integer, primary_key=True)

        # Identity ------------------------------------------------------- #
        reference_id = db.Column(db.String(40), nullable=False, index=True)
        user_id = db.Column(db.Integer, nullable=False, index=True)
        property_id = db.Column(db.Integer, nullable=True, index=True)
        landlord_id = db.Column(db.Integer, nullable=True, index=True)
        tax_year = db.Column(db.String(8), nullable=False)

        # Timing --------------------------------------------------------- #
        generated_at = db.Column(
            db.DateTime,
            nullable=False,
            default=lambda: datetime.now(timezone.utc),
        )
        template_version = db.Column(db.String(16), nullable=False)

        # Term ----------------------------------------------------------- #
        term_start = db.Column(db.Date, nullable=False)
        term_end = db.Column(db.Date, nullable=False)
        term_days = db.Column(db.Integer, nullable=False)

        # Rent ----------------------------------------------------------- #
        currency = db.Column(db.String(8), nullable=False, default="LKR")
        monthly_rent_lkr = db.Column(db.Numeric(12, 2), nullable=False)
        home_office_percentage = db.Column(db.Numeric(5, 4), nullable=True)
        home_office_portion_lkr = db.Column(db.Numeric(12, 2), nullable=True)

        # §195 audit trail ---------------------------------------------- #
        s195_disclosure_applied = db.Column(db.Boolean, nullable=False, default=False)
        s195_default_on_recommended = db.Column(db.Boolean, nullable=False, default=False)
        s195_override_reason = db.Column(db.Text, nullable=True)
        s195_confidence = db.Column(db.Numeric(5, 4), nullable=True)
        s195_audit_substance_risk = db.Column(db.String(16), nullable=True)
        s195_signals_csv = db.Column(db.Text, nullable=True)

        # Stamp duty ---------------------------------------------------- #
        stamp_duty_chargeable = db.Column(db.Boolean, nullable=False, default=False)
        stamp_duty_lkr = db.Column(db.Numeric(12, 2), nullable=True)
        stamp_duty_band = db.Column(db.String(32), nullable=True)

        # PDF artefact -------------------------------------------------- #
        pdf_sha256 = db.Column(db.String(64), nullable=False)
        pdf_path = db.Column(db.String(255), nullable=True)
        pdf_size_bytes = db.Column(db.Integer, nullable=False)

        def __repr__(self) -> str:  # pragma: no cover
            return (
                f"<RentalAgreementGenerated id={self.id} "
                f"ref={self.reference_id!r}>"
            )


# --------------------------------------------------------------------------- #
# Pydantic DTOs -- transport / validation layer, app-context-free
# --------------------------------------------------------------------------- #


class Party(BaseModel):
    """Common fields shared by Landlord + Tenant."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=200)
    nic: str | None = None
    tin: str | None = None
    address_line: str = Field(min_length=4, max_length=400)
    bank_name: str | None = None
    bank_account: str | None = None


class Property(BaseModel):
    """The rented premises."""

    model_config = ConfigDict(extra="forbid")

    address_line: str = Field(min_length=4, max_length=400)
    lot_plan: str | None = None
    area_sqft: float | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=600)


class RentalAgreementInput(BaseModel):
    """Validated input bundle for render_rental_agreement.

    Carries enough state for the template + persisted audit row. No
    Flask-/SQLAlchemy-specific fields.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: int
    user_name: str = Field(min_length=1)
    tax_year: str = Field(min_length=2, max_length=8)  # e.g. "25-26"

    tenant: Party
    landlord: Party
    property: Property

    term_start: date
    term_end: date
    monthly_rent_lkr: Decimal = Field(gt=0)
    currency: str = Field(default="LKR", min_length=3, max_length=8)
    deposit_months: float = Field(default=2.0, ge=0, le=12)
    deposit_return_days: int = Field(default=30, ge=1, le=365)
    rent_due_day: int = Field(default=1, ge=1, le=28)
    termination_notice_months: int = Field(default=2, ge=1, le=12)
    rent_arrears_days: int = Field(default=14, ge=1, le=180)

    # Home office split -- 1.0 means whole premises for business.
    home_office_percentage: float = Field(default=1.0, gt=0, le=1.0)

    # §195 override surface ------------------------------------------- #
    s195_force_on: bool = False     # CEO/customer can flip on regardless
    s195_force_off: bool = False    # acknowledged override; reason required
    s195_override_reason: str | None = Field(default=None, max_length=1000)
    s195_stated_basis: str | None = Field(default=None, max_length=300)

    # Owner-occupant flag: customer rents from a corporate / self-managed
    # entity that they themselves own. Always defaults §195 on.
    customer_status_owner_rented_from_self: bool = False

    # Misc ----------------------------------------------------------- #
    notice_email: str | None = None
    court_district: str = "Colombo"
    show_draft_banner: bool = True   # flips off after Lanka.tax legal pass

    @field_validator("term_end")
    @classmethod
    def _term_end_after_start(cls, v: date, info: Any) -> date:
        start = info.data.get("term_start")
        if start is not None and v <= start:
            raise ValueError("term_end must be after term_start")
        return v

    @model_validator(mode="after")
    def _override_reason_when_forcing_off(self) -> "RentalAgreementInput":
        if self.s195_force_off and not self.s195_override_reason:
            raise ValueError(
                "s195_override_reason is required when s195_force_off=True "
                "(audit trail)"
            )
        if self.s195_force_on and self.s195_force_off:
            raise ValueError(
                "s195_force_on and s195_force_off are mutually exclusive"
            )
        return self

    @property
    def term_days(self) -> int:
        return (self.term_end - self.term_start).days

    @property
    def home_office_portion_lkr(self) -> Decimal:
        pct = Decimal(str(self.home_office_percentage))
        return (self.monthly_rent_lkr * pct).quantize(Decimal("0.01"))


class RentalAgreementGeneratedSchema(BaseModel):
    """Output projection of the persisted RentalAgreementGenerated row."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int | None = None
    reference_id: str
    user_id: int
    tax_year: str
    template_version: str
    term_start: date
    term_end: date
    term_days: int
    currency: str
    monthly_rent_lkr: Decimal
    home_office_percentage: float | None
    home_office_portion_lkr: Decimal | None
    s195_disclosure_applied: bool
    s195_default_on_recommended: bool
    s195_override_reason: str | None
    s195_confidence: float | None
    s195_audit_substance_risk: Literal["low", "medium", "high"] | None
    stamp_duty_chargeable: bool
    stamp_duty_lkr: Decimal | None
    stamp_duty_band: str | None
    pdf_sha256: str
    pdf_path: str | None
    pdf_size_bytes: int
    generated_at: datetime


__all__ = [
    "Party",
    "Property",
    "RentalAgreementGeneratedSchema",
    "RentalAgreementInput",
]
if _HAS_APP_DB:  # pragma: no branch
    __all__.extend(["ServiceAgreement", "RentalAgreementGenerated"])
