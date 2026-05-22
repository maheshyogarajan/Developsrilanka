"""fiesta.assets_liabilities.models — AssetEntry + LiabilityEntry SQLAlchemy models.

Feature 9 D6 (PLAN_X9_COMPLETION §5).

Two tables persisted in the shared `app.db` session:

  fiesta_asset_entry      — one row per declared asset
  fiesta_liability_entry  — one row per declared liability

Design decisions:
  - Money in cents (integer) to avoid float drift; exposed as Decimal LKR
    via .value_lkr / .balance_lkr / .original_amount_lkr properties.
  - tax_year string "2025/2026" matches the rest of FIESTA.
  - user_id is a plain integer FK to the `user` table — same pattern as
    fiesta.deductions.models and fiesta.property.models.
  - fa_submission_id on AssetEntry stores the FA 5192455 submission ID
    when a push succeeds (D9 — avoids a second migration).
  - Standalone fallback for unit-test paths that don't have a full Flask
    context (identical pattern to deductions + property models).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from app import db
    from sqlalchemy import Index, UniqueConstraint
    _HAS_APP = True
except Exception as exc:  # pragma: no cover — unit-test path
    logger.warning(
        "assets_liabilities/models.py: app.db not available — standalone Base: %s", exc
    )
    from sqlalchemy.orm import declarative_base
    from sqlalchemy import (
        Column, Integer, String, Boolean, Date, DateTime, Index, UniqueConstraint,
    )

    class _StandaloneDb:
        Model = declarative_base()
        Column = Column
        Integer = Integer
        String = String
        Boolean = Boolean
        Date = Date
        DateTime = DateTime

    db = _StandaloneDb()  # type: ignore[assignment]
    _HAS_APP = False


# ---------------------------------------------------------------------------
# Asset categories (IRD-adjacent — auditable)
# ---------------------------------------------------------------------------
ASSET_CATEGORIES = (
    "cash_and_bank",
    "fixed_deposits",
    "unit_trusts",
    "shares_and_securities",
    "real_property",
    "vehicles",
    "foreign_assets",
    "other",
)

# ---------------------------------------------------------------------------
# Liability categories
# ---------------------------------------------------------------------------
LIABILITY_CATEGORIES = (
    "mortgage",
    "bank_loan",
    "personal_loan",
    "credit_card",
    "hire_purchase",
    "foreign_loan",
    "other",
)


# ---------------------------------------------------------------------------
# AssetEntry
# ---------------------------------------------------------------------------
class AssetEntry(db.Model):  # type: ignore[name-defined,misc]
    """One row per declared asset item for a (user, tax_year).

    Stored value: value_lkr_cents (integer). Use .value_lkr property for
    Decimal LKR — suitable for templates and PDF rendering.
    """

    __tablename__ = "fiesta_asset_entry"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership ----------------------------------------------------------------
    user_id = db.Column(db.Integer, nullable=False, index=True)
    tax_year = db.Column(db.String(16), nullable=False, index=True)  # "2025/2026"

    # Classification -----------------------------------------------------------
    category = db.Column(db.String(48), nullable=False)          # from ASSET_CATEGORIES
    description = db.Column(db.String(512), nullable=False)      # free text

    # Value -------------------------------------------------------------------
    # Cents to avoid float drift.  value_lkr property converts for callers.
    value_lkr_cents = db.Column(db.Integer, nullable=False, default=0)

    # Acquisition date (optional — useful for capital-gains timeline)
    acquired_date = db.Column(db.Date, nullable=True)

    # Supporting evidence reference (e.g. S3 document key or FA form ID)
    evidence_ref = db.Column(db.String(256), nullable=True)

    # D9 — FA 5192455 push result
    fa_submission_id = db.Column(db.String(64), nullable=True)

    # Audit -------------------------------------------------------------------
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_fiesta_asset_entry_user_year", "user_id", "tax_year"),
    )

    # ------------------------------------------------------------------
    # Decimal LKR helper
    # ------------------------------------------------------------------
    @property
    def value_lkr(self) -> Decimal:
        """Decimal LKR value (cents → LKR, 2 decimal places)."""
        return (Decimal(self.value_lkr_cents or 0) / Decimal(100)).quantize(
            Decimal("0.01")
        )

    @value_lkr.setter
    def value_lkr(self, value) -> None:
        if value is None:
            self.value_lkr_cents = 0
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.value_lkr_cents = int((value * 100).to_integral_value())

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tax_year": self.tax_year,
            "category": self.category,
            "description": self.description,
            "value_lkr": str(self.value_lkr),
            "acquired_date": self.acquired_date.isoformat() if self.acquired_date else None,
            "evidence_ref": self.evidence_ref,
            "fa_submission_id": self.fa_submission_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AssetEntry user={self.user_id} year={self.tax_year} "
            f"cat={self.category} val={self.value_lkr}>"
        )


# ---------------------------------------------------------------------------
# LiabilityEntry
# ---------------------------------------------------------------------------
class LiabilityEntry(db.Model):  # type: ignore[name-defined,misc]
    """One row per declared liability item for a (user, tax_year).

    Money fields:
        balance_lkr_cents         — outstanding balance at tax-year end (cents)
        original_amount_lkr_cents — original loan amount at inception (cents)
    """

    __tablename__ = "fiesta_liability_entry"

    id = db.Column(db.Integer, primary_key=True)

    # Ownership ----------------------------------------------------------------
    user_id = db.Column(db.Integer, nullable=False, index=True)
    tax_year = db.Column(db.String(16), nullable=False, index=True)  # "2025/2026"

    # Classification -----------------------------------------------------------
    category = db.Column(db.String(48), nullable=False)          # from LIABILITY_CATEGORIES
    description = db.Column(db.String(512), nullable=False)
    lender = db.Column(db.String(256), nullable=True)            # bank / institution name

    # Value -------------------------------------------------------------------
    balance_lkr_cents = db.Column(db.Integer, nullable=False, default=0)
    original_amount_lkr_cents = db.Column(db.Integer, nullable=True)

    # Due / maturity date (optional)
    due_date = db.Column(db.Date, nullable=True)

    # Audit -------------------------------------------------------------------
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_fiesta_liability_entry_user_year", "user_id", "tax_year"),
    )

    # ------------------------------------------------------------------
    # Decimal LKR helpers
    # ------------------------------------------------------------------
    @property
    def balance_lkr(self) -> Decimal:
        return (Decimal(self.balance_lkr_cents or 0) / Decimal(100)).quantize(
            Decimal("0.01")
        )

    @balance_lkr.setter
    def balance_lkr(self, value) -> None:
        if value is None:
            self.balance_lkr_cents = 0
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.balance_lkr_cents = int((value * 100).to_integral_value())

    @property
    def original_amount_lkr(self) -> Optional[Decimal]:
        if self.original_amount_lkr_cents is None:
            return None
        return (Decimal(self.original_amount_lkr_cents) / Decimal(100)).quantize(
            Decimal("0.01")
        )

    @original_amount_lkr.setter
    def original_amount_lkr(self, value) -> None:
        if value is None:
            self.original_amount_lkr_cents = None
            return
        if isinstance(value, (int, float)):
            value = Decimal(str(value))
        self.original_amount_lkr_cents = int((value * 100).to_integral_value())

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "tax_year": self.tax_year,
            "category": self.category,
            "description": self.description,
            "lender": self.lender,
            "balance_lkr": str(self.balance_lkr),
            "original_amount_lkr": str(self.original_amount_lkr) if self.original_amount_lkr else None,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<LiabilityEntry user={self.user_id} year={self.tax_year} "
            f"cat={self.category} bal={self.balance_lkr}>"
        )
