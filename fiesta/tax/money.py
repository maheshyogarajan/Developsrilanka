"""fiesta.tax.money — canonical Money value object (Design Lock 2 §1).

BINDING shape. Every income / disposal / credit amount in the tax engine
uses this exact dataclass. LKR is derived from foreign amount * fx_rate so
downstream tax computations always operate on LKR.

Used by:
  - Income (any source) — flat columns mirror this shape
  - AssetDisposal (acquisition + disposal) — flat columns mirror this shape
  - ForeignTaxCredit (credit_lkr)

Provenance: Design Lock 2 §1 (Council convergence, 2026-05-25).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Money:
    """Canonical money value object.

    Fields (locked):
        amount      — native amount in `currency`
        currency    — ISO-4217 (LKR, USD, GBP, AUD, EUR, ...)
        fx_rate     — rate to LKR at `fx_date` (1.0 if currency == 'LKR')
        fx_source   — "CBSL" | "manual" | "stripe_payout" | "bank_statement"
                       | "user_entry" | "lkr_native"
        fx_date     — rate observation date
        amount_lkr  — DERIVED: amount * fx_rate, quantised to 2 dp.
    """

    amount: Decimal
    currency: str
    fx_rate: Decimal
    fx_source: str
    fx_date: date
    amount_lkr: Decimal = field(init=False)

    def __post_init__(self) -> None:
        # Use object.__setattr__ to bypass frozen=True for the derived field.
        object.__setattr__(
            self,
            "amount_lkr",
            (self.amount * self.fx_rate).quantize(Decimal("0.01")),
        )

    @classmethod
    def lkr(cls, amount: Decimal, fx_date: date) -> "Money":
        """Convenience constructor for LKR-native amounts.

        Sets fx_rate=1.0, fx_source='lkr_native'. The derived amount_lkr
        equals `amount` quantised to 2 dp.
        """
        return cls(
            amount=Decimal(amount),
            currency="LKR",
            fx_rate=Decimal("1.0"),
            fx_source="lkr_native",
            fx_date=fx_date,
        )

    def to_dict(self) -> dict:
        """Serialise for JSON storage (e.g. evidence_refs payloads).

        All numerics emitted as strings to preserve Decimal precision.
        Round-trip via Money(**{...with decimals/date parsed...}).
        """
        return {
            "amount": str(self.amount),
            "currency": self.currency,
            "fx_rate": str(self.fx_rate),
            "fx_source": self.fx_source,
            "fx_date": self.fx_date.isoformat(),
            "amount_lkr": str(self.amount_lkr),
        }


__all__ = ["Money"]
