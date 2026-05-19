"""fiesta.tax.types — Pydantic + dataclass types for the SL PIT engine.

Type-strict surface for the tax engine. All currency is Decimal (LKR).
Float is forbidden in this module — bracket arithmetic must be exact to the
cent, and float introduces drift that fails the 0.00-LKR regression gate.

Sources:
  - FIESTA brief tax_math_anchors (council brief 2026-05-19)
  - Inland Revenue (Amendment) Act gazette 25/26
  - Inland Revenue Act §51 (senior-citizen extra relief)

Naming:
  - TaxYearSlabs: shape of one tax-year entry in slabs.yaml
  - Income: gross income components (employment, foreign, rental, FD, etc.)
  - Deductions: input deductions before relief (solar, etc.)
  - Reliefs: computed/applied relief amounts
  - BracketResult: per-band slice + tax
  - TaxComputation: full audit-trail return object
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator


class TaxYear(str, Enum):
    """Supported tax years. Keys match slabs.yaml top-level entries."""

    Y24_25 = "24_25"
    Y25_26 = "25_26"


class Bracket(BaseModel):
    """One bracket band in a TaxYearSlabs.

    `up_to=None` denotes an open-ended top band. `rate` is the marginal rate
    applied to the slice of taxable income above the previous band's `up_to`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    up_to: Optional[NonNegativeInt] = Field(
        default=None,
        description="Top of the band in LKR (inclusive). None = open-ended.",
    )
    rate: Decimal = Field(
        ...,
        description="Marginal rate applied to the slice in this band. e.g. 0.06.",
        ge=Decimal("0"),
        le=Decimal("1"),
    )

    @field_validator("rate", mode="before")
    @classmethod
    def _coerce_rate_to_decimal(cls, v: object) -> Decimal:
        # YAML loaders return float for "0.06"; coerce to Decimal via str so
        # we don't inherit float's representation error.
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        raise TypeError(f"Cannot coerce {type(v).__name__} to Decimal rate")


class TaxYearSlabs(BaseModel):
    """Shape of one tax-year entry in slabs.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: TaxYear
    personal_relief_lkr: NonNegativeInt
    senior_citizen_extra_relief_lkr: NonNegativeInt = 0
    brackets: tuple[Bracket, ...]


class Income(BaseModel):
    """Gross income components in LKR.

    Engine sums these to assessable_income before any relief. All non-negative.
    Foreign income should be pre-converted to LKR by the FX module (Phase 2)
    before reaching the engine — engine is offline-computable, currency-naive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    employment_lkr: Decimal = Decimal("0")
    business_lkr: Decimal = Decimal("0")
    foreign_lkr: Decimal = Decimal("0")
    rental_lkr: Decimal = Decimal("0")
    fd_interest_lkr: Decimal = Decimal("0")
    investment_lkr: Decimal = Decimal("0")  # unit trusts, T-bills, T-bonds
    other_lkr: Decimal = Decimal("0")

    @field_validator(
        "employment_lkr",
        "business_lkr",
        "foreign_lkr",
        "rental_lkr",
        "fd_interest_lkr",
        "investment_lkr",
        "other_lkr",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, v: object) -> Decimal:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        raise TypeError(f"Cannot coerce {type(v).__name__} to Decimal")

    @field_validator(
        "employment_lkr",
        "business_lkr",
        "foreign_lkr",
        "rental_lkr",
        "fd_interest_lkr",
        "investment_lkr",
        "other_lkr",
    )
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < 0:
            raise ValueError("Income component cannot be negative")
        return v

    def total_assessable(self) -> Decimal:
        """Sum of all income components — engine's `assessable_income` input."""
        return (
            self.employment_lkr
            + self.business_lkr
            + self.foreign_lkr
            + self.rental_lkr
            + self.fd_interest_lkr
            + self.investment_lkr
            + self.other_lkr
        )


class Deductions(BaseModel):
    """Pre-relief deductions in LKR.

    These reduce assessable income BEFORE personal relief is applied (matching
    the SF flow's FMLtaxableIncome = MAX(0, assessable - (rent + expenditure +
    solar + tax_free_allowance)) but with relief carried separately so we can
    audit the relief delta against gazette changes).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    solar_investment_lkr: Decimal = Decimal("0")  # capped at 600K per gazette
    rent_relief_lkr: Decimal = Decimal("0")
    expenditure_relief_lkr: Decimal = Decimal("0")

    @field_validator(
        "solar_investment_lkr",
        "rent_relief_lkr",
        "expenditure_relief_lkr",
        mode="before",
    )
    @classmethod
    def _to_decimal(cls, v: object) -> Decimal:
        if isinstance(v, Decimal):
            return v
        if isinstance(v, (int, float)):
            return Decimal(str(v))
        if isinstance(v, str):
            return Decimal(v)
        raise TypeError(f"Cannot coerce {type(v).__name__} to Decimal")


class Reliefs(BaseModel):
    """Reliefs as applied (not as input — engine computes the applied amount).

    `personal_relief_applied` is the gazette personal relief for the year
    (1.2M for 24/25, 1.8M for 25/26). `senior_citizen_extra` adds 500K for
    resident individuals aged 60+ in 25/26 (zero in 24/25 per the SF flow).
    `solar_relief_applied` is the deduction after the 600K cap is applied.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    personal_relief_applied_lkr: Decimal
    senior_citizen_extra_lkr: Decimal = Decimal("0")
    solar_relief_applied_lkr: Decimal = Decimal("0")
    rent_relief_applied_lkr: Decimal = Decimal("0")
    expenditure_relief_applied_lkr: Decimal = Decimal("0")

    def total(self) -> Decimal:
        return (
            self.personal_relief_applied_lkr
            + self.senior_citizen_extra_lkr
            + self.solar_relief_applied_lkr
            + self.rent_relief_applied_lkr
            + self.expenditure_relief_applied_lkr
        )


@dataclass(frozen=True)
class BracketResult:
    """Per-band audit-trail entry.

    `band_lower`: inclusive lower bound (0 for first band, previous band's
       up_to for subsequent bands).
    `band_upper`: inclusive upper bound (the band's `up_to`; None if top band).
    `income_in_band`: slice of taxable income that falls in this band.
    `rate`: marginal rate.
    `tax_in_band`: income_in_band * rate (Decimal).
    """

    band_lower: Decimal
    band_upper: Optional[Decimal]
    income_in_band: Decimal
    rate: Decimal
    tax_in_band: Decimal


@dataclass(frozen=True)
class TaxComputation:
    """Full computation result with audit trail.

    Returned by `compute_tax_25_26()` (and any future-year wrapper). All amounts
    Decimal. `by_band` is the per-bracket slice that lets S12 render a band-by-
    band breakdown to defuse the "scam radar" risk (council THE_PATH Risk A).
    """

    tax_year: TaxYear
    gross_income_lkr: Decimal
    deductions_input_lkr: Decimal
    relief_applied: Reliefs
    taxable_income_lkr: Decimal
    by_band: tuple[BracketResult, ...]
    gross_tax_lkr: Decimal
    net_tax_due_lkr: Decimal
    marginal_rate: Decimal
    effective_rate: Decimal

    def to_dict(self) -> dict:
        """Serialise for JSON API responses."""
        return {
            "tax_year": self.tax_year.value,
            "gross_income_lkr": str(self.gross_income_lkr),
            "deductions_input_lkr": str(self.deductions_input_lkr),
            "relief_applied": {
                "personal_relief_lkr": str(self.relief_applied.personal_relief_applied_lkr),
                "senior_citizen_extra_lkr": str(self.relief_applied.senior_citizen_extra_lkr),
                "solar_relief_lkr": str(self.relief_applied.solar_relief_applied_lkr),
                "rent_relief_lkr": str(self.relief_applied.rent_relief_applied_lkr),
                "expenditure_relief_lkr": str(self.relief_applied.expenditure_relief_applied_lkr),
                "total_lkr": str(self.relief_applied.total()),
            },
            "taxable_income_lkr": str(self.taxable_income_lkr),
            "by_band": [
                {
                    "band_lower_lkr": str(b.band_lower),
                    "band_upper_lkr": str(b.band_upper) if b.band_upper is not None else None,
                    "income_in_band_lkr": str(b.income_in_band),
                    "rate": str(b.rate),
                    "tax_in_band_lkr": str(b.tax_in_band),
                }
                for b in self.by_band
            ],
            "gross_tax_lkr": str(self.gross_tax_lkr),
            "net_tax_due_lkr": str(self.net_tax_due_lkr),
            "marginal_rate": str(self.marginal_rate),
            "effective_rate": str(self.effective_rate),
        }


__all__ = [
    "TaxYear",
    "Bracket",
    "TaxYearSlabs",
    "Income",
    "Deductions",
    "Reliefs",
    "BracketResult",
    "TaxComputation",
]
