"""fiesta.property.models — Property + Landlord + RentalAgreement +
LandlordRelationshipDetection (SQLAlchemy).

S7 Property Owner persists:
    Property — physical property + home-office allocation
    Landlord — payee for the rent (often the customer themselves)
    RentalAgreement — monthly rent + dates + payment terms (consumed by S9
        to draft the actual agreement PDF)
    LandlordRelationshipDetection — §195 detector snapshot for audit trail

DB compatibility: written against `from app import db` so it joins the
existing migration framework. Tables created via db.create_all() or
explicit migration. Standalone test path uses an in-memory SQLAlchemy
declarative base so the module imports headless.

Money in cents (integer) to avoid float drift — Decimal LKR via properties.

A note on multiplicity: most customers have one property. The schema
supports N because (a) some FIESTA customers will be landlords themselves
of investment property as well as having a home office in their primary
residence; (b) couples splitting deduction sometimes need separate property
records keyed to spouse's earnings.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from app import db
    from sqlalchemy import ForeignKey, Index, UniqueConstraint
    _HAS_APP = True
except Exception as exc:  # pragma: no cover — isolated unit-test path
    logger.warning("property/models.py: app.db not available — standalone Base: %s", exc)
    from sqlalchemy.orm import declarative_base, relationship as _rel
    from sqlalchemy import (
        Column, Integer, String, Boolean, DateTime, Date, ForeignKey, Index,
        UniqueConstraint, Float,
    )

    class _StandaloneDb:
        Model = declarative_base()
        Column = Column
        Integer = Integer
        String = String
        Boolean = Boolean
        DateTime = DateTime
        Date = Date
        Float = Float
        ForeignKey = ForeignKey
        relationship = staticmethod(_rel)

    db = _StandaloneDb()  # type: ignore[assignment]
    _HAS_APP = False


# ---------------------------------------------------------------------------
# Enums (string-typed to keep migrations + reads simple)
# ---------------------------------------------------------------------------

PROPERTY_TYPES = ("apartment", "house", "villa", "condo", "annex", "other")

PURPOSES = ("residence", "business", "mixed")

CUSTOMER_STATUSES = (
    "tenant",              # customer pays rent to a third party
    "owner-occupant",      # customer owns + lives in the property
    "owner-rented-out",    # customer owns + rents the unit out
)

RELATIONSHIPS = (
    "arm's-length",
    "family",
    "friend",
    "spouse",
    "parent",
    "sibling",
    "child",
    "self-owns",
    "business-associate",
)

PAYMENT_METHODS = ("cash", "transfer", "cheque", "standing-order")

PAYMENT_FREQUENCIES = ("monthly", "quarterly", "annual")

DOCUMENT_STATUSES = (
    "not-generated",
    "draft",
    "signed",
    "notarized",
)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------

class Property(db.Model):  # type: ignore[name-defined,misc]
    """A physical property a customer claims home-office or rental income on.

    home_office_percentage is auto-computed from home_office_sqft / total_sqft
    at write time (see _recompute_home_office_percentage). If both inputs are
    null the field stays None.
    """

    __tablename__ = "fiesta_property"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership
    user_id = db.Column(db.Integer, nullable=False, index=True)

    # Address
    address_line1 = db.Column(db.String(256), nullable=False)
    address_line2 = db.Column(db.String(256), nullable=True)
    city = db.Column(db.String(128), nullable=False)
    postcode = db.Column(db.String(16), nullable=True)

    # Property type
    property_type = db.Column(db.String(32), nullable=False, default="apartment")
    purpose = db.Column(db.String(16), nullable=False, default="mixed")
    customer_status = db.Column(db.String(32), nullable=False, default="tenant")

    # Square-footage allocation
    total_sqft = db.Column(db.Integer, nullable=True)
    home_office_sqft = db.Column(db.Integer, nullable=True)
    home_office_percentage = db.Column(db.Float, nullable=True)

    # Audit
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_fiesta_property_user", "user_id"),
    )

    # ------------------------------------------------------------------
    # Computed-field hook.
    # ------------------------------------------------------------------
    def recompute_home_office_percentage(self) -> None:
        """Update home_office_percentage from sqft pair. Idempotent."""
        if self.total_sqft and self.home_office_sqft and self.total_sqft > 0:
            pct = (float(self.home_office_sqft) / float(self.total_sqft)) * 100.0
            self.home_office_percentage = round(pct, 2)
        elif self.total_sqft and self.total_sqft > 0 and (
            self.home_office_sqft is None or self.home_office_sqft == 0
        ):
            self.home_office_percentage = 0.0
        else:
            self.home_office_percentage = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "postcode": self.postcode,
            "property_type": self.property_type,
            "purpose": self.purpose,
            "customer_status": self.customer_status,
            "total_sqft": self.total_sqft,
            "home_office_sqft": self.home_office_sqft,
            "home_office_percentage": self.home_office_percentage,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Property id={self.id} user={self.user_id} city={self.city}>"


# ---------------------------------------------------------------------------
# Landlord
# ---------------------------------------------------------------------------

class Landlord(db.Model):  # type: ignore[name-defined,misc]
    """Landlord receiving the rent.

    For owner-occupant who is "renting from themselves" (legitimate where the
    customer's company pays them rent for using a room), the landlord row IS
    the customer — full_name + NIC mirror the user profile and
    relationship_to_customer="self-owns". §195 disclosure DEFAULTS ON.
    """

    __tablename__ = "fiesta_landlord"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership
    user_id = db.Column(db.Integer, nullable=False, index=True)
    property_id = db.Column(
        db.Integer,
        db.ForeignKey("fiesta_property.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Identity
    full_name = db.Column(db.String(256), nullable=False)
    nic = db.Column(db.String(32), nullable=True)
    tin = db.Column(db.String(32), nullable=True)

    # Contact
    address = db.Column(db.String(512), nullable=True)
    phone = db.Column(db.String(32), nullable=True)
    email = db.Column(db.String(256), nullable=True)

    # Payee bank
    bank_name = db.Column(db.String(128), nullable=True)
    bank_account_number = db.Column(db.String(64), nullable=True)

    # Relationship — drives §195 default-on
    relationship_to_customer = db.Column(
        db.String(32), nullable=False, default="arm's-length"
    )

    # Audit
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "property_id", "full_name",
            name="uq_fiesta_landlord_property_name",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "property_id": self.property_id,
            "full_name": self.full_name,
            "nic": self.nic,
            "tin": self.tin,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "bank_name": self.bank_name,
            "bank_account_number": self.bank_account_number,
            "relationship_to_customer": self.relationship_to_customer,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Landlord id={self.id} property={self.property_id} "
            f"rel={self.relationship_to_customer}>"
        )


# ---------------------------------------------------------------------------
# RentalAgreement
# ---------------------------------------------------------------------------

DEFAULT_AGREEMENT_DAYS = 364  # stay under stamp-duty threshold (12 months → duty)


class RentalAgreement(db.Model):  # type: ignore[name-defined,misc]
    """Rental agreement keyed (property, landlord, tax_year-ish).

    home_office_portion_lkr = monthly_rent_lkr * (home_office_percentage / 100)
    is recomputed on every write. Stamp-duty default: end_date = start_date +
    364 days (NOT 365 — keeps it under 12 months for stamp duty purposes).
    """

    __tablename__ = "fiesta_rental_agreement"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership / FKs
    user_id = db.Column(db.Integer, nullable=False, index=True)
    property_id = db.Column(
        db.Integer,
        db.ForeignKey("fiesta_property.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    landlord_id = db.Column(
        db.Integer,
        db.ForeignKey("fiesta_landlord.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Dates
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    end_date = db.Column(db.Date, nullable=False)

    # Rent (cents)
    monthly_rent_lkr_cents = db.Column(db.Integer, nullable=False, default=0)
    deposit_paid_cents = db.Column(db.Integer, nullable=True)
    home_office_portion_lkr_cents = db.Column(db.Integer, nullable=True)

    payment_method = db.Column(db.String(16), nullable=False, default="transfer")
    payment_frequency = db.Column(db.String(16), nullable=False, default="monthly")

    document_status = db.Column(
        db.String(16), nullable=False, default="not-generated"
    )

    # Tax year context
    tax_year = db.Column(db.String(16), nullable=False, index=True, default="2025/2026")

    # Audit
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_fiesta_rental_user_year", "user_id", "tax_year"),
    )

    # ------------------------------------------------------------------
    # Decimal LKR helpers (cents -> LKR).
    # ------------------------------------------------------------------
    @staticmethod
    def _cents_to_lkr(c: Optional[int]) -> Optional[Decimal]:
        if c is None:
            return None
        return (Decimal(c) / Decimal(100)).quantize(Decimal("0.01"))

    @staticmethod
    def _lkr_to_cents(v) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            v = Decimal(str(v))
        return int((v * 100).to_integral_value())

    @property
    def monthly_rent_lkr(self) -> Optional[Decimal]:
        return self._cents_to_lkr(self.monthly_rent_lkr_cents)

    @monthly_rent_lkr.setter
    def monthly_rent_lkr(self, value) -> None:
        self.monthly_rent_lkr_cents = self._lkr_to_cents(value) or 0

    @property
    def deposit_paid(self) -> Optional[Decimal]:
        return self._cents_to_lkr(self.deposit_paid_cents)

    @deposit_paid.setter
    def deposit_paid(self, value) -> None:
        self.deposit_paid_cents = self._lkr_to_cents(value)

    @property
    def home_office_portion_lkr(self) -> Optional[Decimal]:
        return self._cents_to_lkr(self.home_office_portion_lkr_cents)

    @home_office_portion_lkr.setter
    def home_office_portion_lkr(self, value) -> None:
        self.home_office_portion_lkr_cents = self._lkr_to_cents(value)

    # ------------------------------------------------------------------
    # Computed-field hook + stamp-duty default.
    # ------------------------------------------------------------------
    def apply_defaults(self, property_obj: Optional[Property]) -> None:
        """Stamp-duty default end-date + home-office-portion recompute."""
        if self.end_date is None and self.start_date is not None:
            self.end_date = self.start_date + timedelta(days=DEFAULT_AGREEMENT_DAYS)
        self.recompute_home_office_portion(property_obj)

    def recompute_home_office_portion(self, property_obj: Optional[Property]) -> None:
        if (
            property_obj is not None
            and property_obj.home_office_percentage is not None
            and self.monthly_rent_lkr_cents is not None
        ):
            pct = float(property_obj.home_office_percentage) / 100.0
            self.home_office_portion_lkr_cents = int(
                round(self.monthly_rent_lkr_cents * pct)
            )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "property_id": self.property_id,
            "landlord_id": self.landlord_id,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "monthly_rent_lkr": (
                str(self.monthly_rent_lkr) if self.monthly_rent_lkr is not None else None
            ),
            "deposit_paid": (
                str(self.deposit_paid) if self.deposit_paid is not None else None
            ),
            "home_office_portion_lkr": (
                str(self.home_office_portion_lkr)
                if self.home_office_portion_lkr is not None
                else None
            ),
            "payment_method": self.payment_method,
            "payment_frequency": self.payment_frequency,
            "document_status": self.document_status,
            "tax_year": self.tax_year,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RentalAgreement id={self.id} property={self.property_id} "
            f"rent={self.monthly_rent_lkr}>"
        )


# ---------------------------------------------------------------------------
# LandlordRelationshipDetection — §195 snapshot
# ---------------------------------------------------------------------------

class LandlordRelationshipDetection(db.Model):  # type: ignore[name-defined,misc]
    """Persistent snapshot of fiesta.compliance.related_party output.

    Sibling shape of ServiceProviderRelationship (S6). One row per
    landlord-create / landlord-update event so we can audit-trail how the
    §195 disclosure flag was set at the time the agreement was generated.
    """

    __tablename__ = "fiesta_landlord_relationship_detection"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, nullable=False, index=True)
    landlord_id = db.Column(
        db.Integer,
        db.ForeignKey("fiesta_landlord.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    property_id = db.Column(
        db.Integer,
        db.ForeignKey("fiesta_property.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Snapshot of detector output
    signals_csv = db.Column(db.String(512), nullable=False, default="")
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    should_default_on_disclosure = db.Column(
        db.Boolean, nullable=False, default=False
    )
    audit_substance_risk = db.Column(
        db.String(16), nullable=False, default="low"
    )
    reasoning_json = db.Column(db.String(8192), nullable=False, default="[]")

    # Audit
    detected_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "landlord_id": self.landlord_id,
            "property_id": self.property_id,
            "signals_csv": self.signals_csv,
            "confidence": self.confidence,
            "should_default_on_disclosure": self.should_default_on_disclosure,
            "audit_substance_risk": self.audit_substance_risk,
            "reasoning_json": self.reasoning_json,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LandlordRelationshipDetection landlord={self.landlord_id} "
            f"conf={self.confidence} default_on={self.should_default_on_disclosure}>"
        )
