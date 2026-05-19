"""fiesta.deductions.models — DeductionClaim SQLAlchemy model.

S5 "Reduce your tax — 10 ways" persists one row per (user, tax_year,
category_id) — see catalog.yaml for the 10 category IDs.

The model lives in its own table so the existing flat-layout `Expense`
model (which is the long-term home for actual_lkr amounts with OCR
receipts in S5 Wave 3) is not disturbed. When a customer ticks a
category on the S5 screen, we write a DeductionClaim. When they later
upload a receipt and the Vision pipeline extracts an actual amount,
we write an Expense row and set DeductionClaim.actual_lkr from the
sum of linked expenses.

DB compatibility: This model is intentionally written against the
shared `from app import db` session so it joins the existing migration
framework. The table will be created via db.create_all() (called in
app._ensure_additive_schema()) or by the explicit migration script
(see migrations/add_deduction_claims.py — out of scope for this dispatch).

Indexes:
    - PK on id (implicit)
    - Unique (user_id, tax_year, category_id) — one claim per user-year-category
    - Index on (user_id, tax_year) — primary read path for /reduce-tax

Compliance with FIESTA conventions:
    - Money in cents (integer) to avoid float drift — exposed as Decimal LKR
      through the property `estimated_lkr_decimal` / `actual_lkr_decimal`.
    - created_at + updated_at tracked.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from app import db
    from sqlalchemy import Index, UniqueConstraint
    _HAS_APP = True
except Exception as exc:  # pragma: no cover -- isolated unit-test path
    # In unit-test paths that don't have the full Flask app context, fall
    # back to a vanilla SQLAlchemy declarative base so the test file can
    # still import the module. The real DB lives in app.db when running
    # inside Flask.
    logger.warning("models.py: app.db not available — using standalone Base: %s", exc)
    from sqlalchemy.orm import declarative_base
    from sqlalchemy import (
        Column, Integer, String, Boolean, DateTime, Index, UniqueConstraint,
    )

    class _StandaloneDb:
        Model = declarative_base()
        Column = Column
        Integer = Integer
        String = String
        Boolean = Boolean
        DateTime = DateTime

    db = _StandaloneDb()  # type: ignore[assignment]
    _HAS_APP = False


# ---------------------------------------------------------------------------
# Evidence status enum (string-typed to keep migrations simple).
# ---------------------------------------------------------------------------
EVIDENCE_STATUS_PENDING = "pending"
EVIDENCE_STATUS_COLLECTED = "collected"
EVIDENCE_STATUS_SUBMITTED = "submitted"
EVIDENCE_STATUS_REJECTED = "rejected"

EVIDENCE_STATUSES = (
    EVIDENCE_STATUS_PENDING,
    EVIDENCE_STATUS_COLLECTED,
    EVIDENCE_STATUS_SUBMITTED,
    EVIDENCE_STATUS_REJECTED,
)


class DeductionClaim(db.Model):  # type: ignore[name-defined,misc]
    """One row per (user, tax_year, category) that the customer has claimed.

    Lifecycle:
        1. Customer ticks a card on /reduce-tax        -> claimed=True, evidence_status=pending
        2. Customer uploads receipts (later Wave)      -> evidence_status=collected
        3. Customer files return                       -> evidence_status=submitted
        4. (Optional) IRD queries / rejects            -> evidence_status=rejected

    Money fields:
        estimated_lkr  : Best-guess amount before evidence. Stored as cents.
        actual_lkr     : Evidence-backed amount once collected. Stored as cents.
    """

    __tablename__ = "fiesta_deduction_claim"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership ----------------------------------------------------------
    user_id = db.Column(db.Integer, nullable=False, index=True)
    # Tax year string format matches the rest of FIESTA: "2025/2026"
    tax_year = db.Column(db.String(16), nullable=False, index=True)

    # Category -----------------------------------------------------------
    # Matches catalog.yaml `id` column. Stored as string to avoid a JOIN
    # against a category table — the catalog is YAML-versioned, not
    # database-versioned.
    category_id = db.Column(db.String(48), nullable=False)

    # State --------------------------------------------------------------
    claimed = db.Column(db.Boolean, nullable=False, default=True)
    # Cents — multiply LKR by 100. Nullable because customer may claim
    # a category before settling on an amount.
    estimated_lkr_cents = db.Column(db.Integer, nullable=True)
    actual_lkr_cents = db.Column(db.Integer, nullable=True)

    evidence_status = db.Column(
        db.String(16), nullable=False, default=EVIDENCE_STATUS_PENDING
    )

    notes = db.Column(db.String(1024), nullable=True)

    # Audit --------------------------------------------------------------
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "tax_year", "category_id",
            name="uq_fiesta_deduction_claim_user_year_category",
        ),
        Index(
            "ix_fiesta_deduction_claim_user_year",
            "user_id", "tax_year",
        ),
    )

    # ------------------------------------------------------------------
    # Decimal LKR helpers (cents -> LKR).
    # ------------------------------------------------------------------
    @property
    def estimated_lkr(self) -> Optional[Decimal]:
        """Decimal LKR for use in templates / estimate engine."""
        if self.estimated_lkr_cents is None:
            return None
        return (Decimal(self.estimated_lkr_cents) / Decimal(100)).quantize(Decimal("0.01"))

    @estimated_lkr.setter
    def estimated_lkr(self, value) -> None:
        if value is None:
            self.estimated_lkr_cents = None
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.estimated_lkr_cents = int((value * 100).to_integral_value())

    @property
    def actual_lkr(self) -> Optional[Decimal]:
        if self.actual_lkr_cents is None:
            return None
        return (Decimal(self.actual_lkr_cents) / Decimal(100)).quantize(Decimal("0.01"))

    @actual_lkr.setter
    def actual_lkr(self, value) -> None:
        if value is None:
            self.actual_lkr_cents = None
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.actual_lkr_cents = int((value * 100).to_integral_value())

    # ------------------------------------------------------------------
    # Serialisation.
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tax_year": self.tax_year,
            "category_id": self.category_id,
            "claimed": self.claimed,
            "estimated_lkr": str(self.estimated_lkr) if self.estimated_lkr is not None else None,
            "actual_lkr": str(self.actual_lkr) if self.actual_lkr is not None else None,
            "evidence_status": self.evidence_status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DeductionClaim user={self.user_id} year={self.tax_year} "
            f"cat={self.category_id} claimed={self.claimed}>"
        )
