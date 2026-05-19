"""fiesta.earnings.to_tax — aggregate confirmed IncomeEntry rows for the tax engine.

Shape contract — `income_summary_for_tax_year(user_id, ty)` returns:

    {
        "user_id": int,
        "tax_year": str (e.g. '2025-26'),
        "by_category_lkr": {
            "salary": Decimal,
            "contractor_fee": Decimal,
            "foreign_remittance": Decimal,
            "interest": Decimal,
            "dividend": Decimal,
            "rental": Decimal,
        },
        "by_currency": {
            "LKR": Decimal,    # sum in LKR original
            "USD": Decimal,    # sum in USD original (pre-conversion)
            ...
        },
        "total_lkr": Decimal,
        "entry_count": int,
        "unconverted_currencies": list[str],   # rows we couldn't FX-convert
        "fx_warnings": list[str],
    }

Currency conversion strategy:
  1. If entry.amount_lkr is already set (entered at confirmation time) → use it.
  2. Else, lookup fx_rate_service.get_rate(currency, entry.entry_date) →
     write back amount_lkr + fx_rate fields on the row for audit.
  3. If lookup returns None → leave row LKR-unconverted, record currency in
     unconverted_currencies + an fx_warnings entry. Caller (tax engine) decides
     whether to halt or surface to user for manual rate entry.

This module is compatible with fiesta.tax.engine.compute_tax_25_26 — the
engine reads `by_category_lkr` for IIT computation per Sri Lanka tax law
(salary → APIT credit eligible, foreign_remittance → remittance-basis,
interest → WHT credit eligible, etc.).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from fiesta.earnings.models import IncomeCategory, IncomeEntry

log = logging.getLogger(__name__)


def _try_fx_lookup(currency: str, on_date) -> tuple[Decimal | None, str | None]:
    """Look up an FX rate via fx_rate_service.get_rate.

    Returns (rate, source) — rate in LKR per 1 unit foreign. None on miss
    (no rate available or fx_rate_service unavailable).
    """
    try:
        from fx_rate_service import get_rate as fx_get_rate
        fx = fx_get_rate(currency, on_date)
        if fx is None:
            return None, None
        return Decimal(str(fx.value)), fx.source
    except Exception as exc:
        log.warning("earnings.to_tax: fx_rate_service unavailable: %s", exc)
        return None, None


def income_summary_for_tax_year(user_id: int, tax_year: str) -> dict[str, Any]:
    """Aggregate confirmed IncomeEntry rows for a single user, single tax year."""
    rows = (
        IncomeEntry.query
        .filter(
            IncomeEntry.user_id == user_id,
            IncomeEntry.tax_year == tax_year,
            IncomeEntry.confirmed_by_customer.is_(True),
        )
        .all()
    )

    by_category_lkr: dict[str, Decimal] = {c.value: Decimal("0") for c in IncomeCategory}
    by_currency: dict[str, Decimal] = {}
    total_lkr = Decimal("0")
    unconverted: list[str] = []
    fx_warnings: list[str] = []

    for r in rows:
        cur = (r.currency or "LKR").upper()
        amt = Decimal(str(r.amount)) if r.amount is not None else Decimal("0")
        by_currency[cur] = by_currency.get(cur, Decimal("0")) + amt

        # Resolve LKR-equivalent for this row.
        amt_lkr = Decimal(str(r.amount_lkr)) if r.amount_lkr is not None else None

        if amt_lkr is None:
            if cur == "LKR":
                amt_lkr = amt
                # Idempotently backfill so future calls don't re-decide.
                r.amount_lkr = amt
                r.fx_rate_lkr = Decimal("1")
                r.fx_rate_source = "lkr_native"
            else:
                rate, src = _try_fx_lookup(cur, r.entry_date)
                if rate is None:
                    if cur not in unconverted:
                        unconverted.append(cur)
                    fx_warnings.append(
                        f"No FX rate for {cur} on {r.entry_date.isoformat()} "
                        f"(entry id={r.id}). Manual rate entry required before filing."
                    )
                    continue
                amt_lkr = (amt * rate).quantize(Decimal("0.01"))
                r.amount_lkr = amt_lkr
                r.fx_rate_lkr = rate
                r.fx_rate_source = src

        cat = r.category or IncomeCategory.SALARY.value
        if cat not in by_category_lkr:
            by_category_lkr[cat] = Decimal("0")
        by_category_lkr[cat] += amt_lkr
        total_lkr += amt_lkr

    return {
        "user_id": user_id,
        "tax_year": tax_year,
        "by_category_lkr": by_category_lkr,
        "by_currency": by_currency,
        "total_lkr": total_lkr,
        "entry_count": len(rows),
        "unconverted_currencies": unconverted,
        "fx_warnings": fx_warnings,
    }


__all__ = ["income_summary_for_tax_year"]
