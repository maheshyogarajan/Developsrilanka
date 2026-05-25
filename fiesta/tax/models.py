"""fiesta.tax.models — canonical SQLAlchemy models (Design Lock 2 §4-§5).

This module defines the schema seam every MS2 + MS3 + MS4 income/disposal/
bank-parse/RSU code path reads/writes. Field names, table names, source_type
values, and asset_type values are LOCKED by Design Lock 2.

Tables defined here:
  - incomes                 (§4) — canonical income ledger
  - asset_disposals         (§5) — CGT seam (crypto + equity + fd + real_estate
                                    + rsu + bond + unit_trust + other)
  - parsed_bank_statements  (B8 full impl extends — minimal placeholder)
  - rsu_vesting_events      (B11 extends — minimal placeholder)

NB: there is a separate ``fiesta.tax.types.Income`` Pydantic class used as the
engine input aggregate (sum of components). That class stays unchanged; this
``Income`` here is a per-row ORM model with its own concern (one row per
income event with provenance + DTAA source-country tagging). The two are
distinct on purpose: ``types.Income`` aggregates, ``models.Income`` records.

Provenance: Design Lock 2 §4 + §5 (Council convergence, 2026-05-25).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app import db


# ---------------------------------------------------------------------------
# Locked vocabulary — referenced by classifiers + UI dropdowns
# ---------------------------------------------------------------------------
INCOME_SOURCE_TYPES = (
    "foreign_remittance",
    "employment_lkr",
    "professional_fees_lkr",
    "business_lkr",
    "business_foreign",
    "rsu",
    "crypto",
    "rental_lkr",
    "rental_foreign",
    "investment_lkr",
    "investment_foreign",
    "other",
)

ASSET_DISPOSAL_TYPES = (
    "crypto",
    "equity",
    "fd",
    "real_estate",
    "rsu",
    "bond",
    "unit_trust",
    "other",
)

BANK_PARSE_STATUSES = (
    "pending",
    "parsing",
    "parsed",
    "failed",
    "reviewed",
)


# ---------------------------------------------------------------------------
# Income — canonical per-event ledger (§4)
# ---------------------------------------------------------------------------
class Income(db.Model):
    """Canonical income row. ONE row per income event.

    Every MS2 + MS3 income classifier writes here. Aggregation back into
    the existing ``fiesta.tax.types.Income`` (engine-input) is the engine's
    concern, not this model's.

    Soft-link FKs (remittance_id, bank_parse_id, rsu_vesting_id) are
    nullable so source importers can populate whichever applies.
    """

    __tablename__ = "incomes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)  # "2025/26"

    # Locked vocabulary — see INCOME_SOURCE_TYPES
    source_type = db.Column(db.String(32), nullable=False)

    # Money flat columns (mirror Money value object exactly)
    amount = db.Column(db.Numeric(20, 4), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="LKR")
    fx_rate = db.Column(db.Numeric(20, 8), nullable=False, default=Decimal("1.0"))
    fx_source = db.Column(db.String(32), nullable=False, default="lkr_native")
    fx_date = db.Column(db.Date, nullable=False)
    amount_lkr = db.Column(db.Numeric(20, 2), nullable=False, index=True)

    # DTAA source attribution
    source_country = db.Column(db.String(2), nullable=True)  # ISO-3166-1 alpha-2

    # Provenance — joinable to evidence
    # Example payload: [{"type":"bank_statement_parse","ref_id":42,"page":3},
    #                   {"type":"manual_entry","user_id":1}]
    evidence_refs = db.Column(db.JSON, nullable=False, default=list)

    # Soft-link to source records — nullable; populated by importers
    remittance_id = db.Column(
        db.Integer,
        db.ForeignKey("remittance_entries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    bank_parse_id = db.Column(
        db.Integer,
        db.ForeignKey("parsed_bank_statements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rsu_vesting_id = db.Column(
        db.Integer,
        db.ForeignKey("rsu_vesting_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # B12 (MS3) — soft-link to business_income_entries.id. Nullable for non-
    # business sources. Schema added by migration M3-001.
    business_income_id = db.Column(
        db.Integer,
        db.ForeignKey("business_income_entries.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index("ix_incomes_user_tax_year", "user_id", "tax_year"),
        db.Index("ix_incomes_source_type_tax_year", "source_type", "tax_year"),
    )

    def __repr__(self) -> str:  # pragma: no cover — repr only
        return (
            f"<Income id={self.id} user_id={self.user_id} "
            f"source_type={self.source_type!r} amount_lkr={self.amount_lkr}>"
        )


# ---------------------------------------------------------------------------
# AssetDisposal — generic CGT ledger (§5)
# ---------------------------------------------------------------------------
class AssetDisposal(db.Model):
    """Canonical disposal row for CGT computation.

    B13 crypto + Section G G3.5 (FD/dividend/equity CGT) both write here.
    NO separate ``CryptoDisposal`` table — Design Lock 2 §8 forbids it.

    B11 RSU vesting events write to ``RSUVestingEvent`` (vesting = income at
    fair market value, recorded in Income); subsequent RSU SALES write here
    with ``asset_type='rsu'``.
    """

    __tablename__ = "asset_disposals"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tax_year = db.Column(db.String(7), nullable=False, index=True)

    # Locked vocabulary — see ASSET_DISPOSAL_TYPES
    asset_type = db.Column(db.String(16), nullable=False)

    # Acquisition Money (flat columns)
    acq_amount = db.Column(db.Numeric(20, 4), nullable=False)
    acq_currency = db.Column(db.String(3), nullable=False, default="LKR")
    acq_fx_rate = db.Column(db.Numeric(20, 8), nullable=False, default=Decimal("1.0"))
    acq_fx_source = db.Column(db.String(32), nullable=False, default="lkr_native")
    acq_fx_date = db.Column(db.Date, nullable=False)
    acq_amount_lkr = db.Column(db.Numeric(20, 2), nullable=False)

    # Disposal Money (flat columns)
    disp_amount = db.Column(db.Numeric(20, 4), nullable=False)
    disp_currency = db.Column(db.String(3), nullable=False, default="LKR")
    disp_fx_rate = db.Column(db.Numeric(20, 8), nullable=False, default=Decimal("1.0"))
    disp_fx_source = db.Column(db.String(32), nullable=False, default="lkr_native")
    disp_fx_date = db.Column(db.Date, nullable=False)
    disp_amount_lkr = db.Column(db.Numeric(20, 2), nullable=False)

    # Computed CGT base: disp_amount_lkr - acq_amount_lkr (may be negative)
    gain_lkr = db.Column(db.Numeric(20, 2), nullable=False)

    # Holding period
    acquisition_date = db.Column(db.Date, nullable=False)
    disposal_date = db.Column(db.Date, nullable=False)

    # DTAA seam
    source_country = db.Column(db.String(2), nullable=True)

    # Asset identifier (audit): "BTC"/"ETH" for crypto; ticker for equity; etc.
    asset_identifier = db.Column(db.String(128), nullable=True)

    evidence_refs = db.Column(db.JSON, nullable=False, default=list)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.Index("ix_asset_disposals_user_tax_year", "user_id", "tax_year"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AssetDisposal id={self.id} user_id={self.user_id} "
            f"asset_type={self.asset_type!r} gain_lkr={self.gain_lkr}>"
        )


# ---------------------------------------------------------------------------
# ParsedBankStatement — minimal placeholder; B8 full impl extends
# ---------------------------------------------------------------------------
class ParsedBankStatement(db.Model):
    """Bank-statement parsing record. B8 full impl (downstream subagent)
    extends with parsed_rows, parser_version, confidence_scores, etc.

    For E.0 (schema-first), only the minimum surface needed for the
    ``Income.bank_parse_id`` FK to resolve.
    """

    __tablename__ = "parsed_bank_statements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_ref = db.Column(db.String(512), nullable=False)  # S3 key or local path
    parsed_at = db.Column(db.DateTime, nullable=True)     # set on successful parse
    status = db.Column(db.String(16), nullable=False, default="pending")
    raw_text = db.Column(db.JSON, nullable=True)          # B8 full impl populates

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ParsedBankStatement id={self.id} user_id={self.user_id} "
            f"status={self.status!r}>"
        )


# ---------------------------------------------------------------------------
# RSUVestingEvent — minimal placeholder; B11 extends
# ---------------------------------------------------------------------------
class RSUVestingEvent(db.Model):
    """RSU vesting event. B11 (downstream subagent) extends with grant_id,
    cliff/quarterly tracking, employer entity, vesting tranche, etc.

    For E.0 (schema-first), only the minimum surface needed for the
    ``Income.rsu_vesting_id`` FK to resolve.

    Vesting = income at fair market value; B11 will create both:
      - one row here (vesting event with FMV in fair_market_value_money JSON)
      - one corresponding Income row with source_type='rsu' linked via FK
    Subsequent RSU sales create AssetDisposal rows with asset_type='rsu'.
    """

    __tablename__ = "rsu_vesting_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vesting_date = db.Column(db.Date, nullable=False)
    # B11 extends; serialised Money.to_dict() recommended.
    fair_market_value_money = db.Column(db.JSON, nullable=False)
    ticker = db.Column(db.String(16), nullable=True)
    source_country = db.Column(db.String(2), nullable=True)

    # Back-link to the Income row this vesting created. Nullable because the
    # Income row may be created in the same transaction or slightly later.
    # The FK lives on Income.rsu_vesting_id (not here) to keep the
    # canonical direction (income points to its source).

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RSUVestingEvent id={self.id} user_id={self.user_id} "
            f"ticker={self.ticker!r}>"
        )


__all__ = [
    "Income",
    "AssetDisposal",
    "ParsedBankStatement",
    "RSUVestingEvent",
    "INCOME_SOURCE_TYPES",
    "ASSET_DISPOSAL_TYPES",
    "BANK_PARSE_STATUSES",
]
