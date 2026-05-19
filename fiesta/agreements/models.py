"""fiesta.agreements.models — schema for rental agreements.

Two model surfaces:

  1. SQLAlchemy: RentalAgreementGenerated -- one row per PDF render. Persistent
     audit trail of every customer-generated agreement.
  2. Pydantic v2 DTOs: RentalAgreementInput, RentalAgreementGeneratedSchema --
     validated inputs/outputs for the route layer + tests.

The SQLAlchemy model is wired through the app's existing `db` instance (see
main.py) and registered when fiesta.agreements is imported. It uses the
existing additive-migration pattern (idempotent CREATE TABLE IF NOT EXISTS
in app._ensure_additive_schema, which sweeps all model metadata).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# SQLAlchemy model -- guarded so importing this module does not require the
# Flask app to be initialised. Test suite can run without app context.
# --------------------------------------------------------------------------- #

try:
    from app import db  # type: ignore[import-not-found]
    _HAS_APP_DB = True
except Exception:  # pragma: no cover -- testing path
    db = None  # type: ignore[assignment]
    _HAS_APP_DB = False


if _HAS_APP_DB:  # pragma: no branch -- guarded import

    class RentalAgreementGenerated(db.Model):  # type: ignore[misc]
        """One row per PDF render. Append-only audit trail.

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
    __all__.append("RentalAgreementGenerated")
