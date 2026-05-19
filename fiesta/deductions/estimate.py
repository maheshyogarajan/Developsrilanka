"""fiesta.deductions.estimate — marginal-rate saving engine for S5.

Given a customer's claimed DeductionClaim list + their income + the
applicable tax slabs, return the estimated tax saving.

Math (intuition):
    For each Rs 100 of deduction, the customer saves
        Rs 100 * marginal_tax_rate.
    The marginal rate is the rate on the LAST slab the income reaches.
    A deduction reduces the top of the income, so it slides off at
    the marginal rate — not the average rate.

Constraints applied:
    1. Total deduction cannot exceed gross income (cap at income).
    2. Category-specific caps from catalog.yaml `caps` section:
       a. Solar: absolute cap Rs 600,000.
       b. Charitable donations: percent_of_taxable_income (5%).
          For this estimator we compute against pre-deduction income;
          a real return-prep flow would iterate.
    3. Reliefs (health insurance, charitable donations) are presented
       to the user as "savings" on the same screen, but in the IRA
       they reduce relief — we apply them at the marginal rate as
       a customer-facing approximation. The actual filing engine
       (S12) does the exact maths.

Tax slabs:
    Default 2025/2026 personal slabs (per IRA + amendments to
    31-Mar-2025) are used if no slabs are passed. Callers can pass
    their own slabs for testing.

Return shape:
    {
        "total_deduction_lkr": Decimal,
        "deduction_cap_applied": str | None,  # explanation if cap kicked in
        "marginal_rate": Decimal,             # e.g. Decimal("0.36")
        "estimated_saving_lkr": Decimal,
        "breakdown": [
            {"category_id": "...", "claimed_lkr": Decimal, "after_cap_lkr": Decimal, "saving_lkr": Decimal, "cap_note": str | None},
            ...
        ],
        "income_lkr": Decimal,
    }
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Any, Iterable

from .catalog_loader import get_caps, load_catalog

logger = logging.getLogger(__name__)

# Use 28 significant digits to give us headroom for chained mults.
getcontext().prec = 28

# ---------------------------------------------------------------------------
# Default tax slabs (LK personal income tax 2025/2026).
# Format: list of (upper_bound_lkr, marginal_rate_decimal). The final tuple
# has upper_bound=None to mean "no upper bound" / top slab.
#
# Source: IRA personal-tax schedule. These are illustrative defaults — the
# real filing engine (S12) takes slabs as input from a single source of truth.
# ---------------------------------------------------------------------------
DEFAULT_PERSONAL_SLABS_LKR: list[tuple[Decimal | None, Decimal]] = [
    (Decimal("1200000"),  Decimal("0.00")),  # tax-free threshold
    (Decimal("1700000"),  Decimal("0.06")),
    (Decimal("2200000"),  Decimal("0.12")),
    (Decimal("2700000"),  Decimal("0.18")),
    (Decimal("3200000"),  Decimal("0.24")),
    (Decimal("3700000"),  Decimal("0.30")),
    (None,                Decimal("0.36")),  # top slab
]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def marginal_rate_for_income(
    income_lkr,
    slabs: list[tuple[Decimal | None, Decimal]] | None = None,
) -> Decimal:
    """Return the marginal rate for the LAST rupee of the given income."""
    income = _as_decimal(income_lkr)
    use_slabs = slabs if slabs is not None else DEFAULT_PERSONAL_SLABS_LKR
    # Walk slabs; return rate whose upper_bound >= income (or top slab if none).
    for upper, rate in use_slabs:
        if upper is None or income <= upper:
            return rate
    # Defensive fallback (top slab):
    return use_slabs[-1][1]


def income_tax_for(
    income_lkr,
    slabs: list[tuple[Decimal | None, Decimal]] | None = None,
) -> Decimal:
    """Total tax on the given income across all slabs."""
    income = _as_decimal(income_lkr)
    use_slabs = slabs if slabs is not None else DEFAULT_PERSONAL_SLABS_LKR
    tax = Decimal("0")
    prev_bound = Decimal("0")
    for upper, rate in use_slabs:
        if upper is None:
            slab_amount = max(Decimal("0"), income - prev_bound)
        else:
            slab_amount = max(Decimal("0"), min(income, upper) - prev_bound)
        tax += slab_amount * rate
        if upper is not None and income <= upper:
            break
        if upper is not None:
            prev_bound = upper
    return _round_money(tax)


# ---------------------------------------------------------------------------
# Cap application.
# ---------------------------------------------------------------------------
def _apply_caps(
    claims_by_cat: dict[str, Decimal],
    income_lkr: Decimal,
    caps: dict[str, Any],
) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Return (post-cap amounts, cap_notes by category_id)."""
    capped: dict[str, Decimal] = {}
    cap_notes: dict[str, str] = {}
    for cat_id, amount in claims_by_cat.items():
        cap = caps.get(cat_id)
        if not cap:
            capped[cat_id] = amount
            continue
        if cap.get("type") == "absolute":
            limit = _as_decimal(cap["amount_lkr"])
            if amount > limit:
                capped[cat_id] = limit
                cap_notes[cat_id] = (
                    f"Capped at Rs {limit:,.0f} (statutory limit). "
                    f"Claimed amount Rs {amount:,.0f} exceeds cap."
                )
            else:
                capped[cat_id] = amount
        elif cap.get("type") == "percent_of_taxable_income":
            pct = _as_decimal(cap["percent"]) / Decimal("100")
            limit = income_lkr * pct
            if amount > limit:
                capped[cat_id] = limit
                cap_notes[cat_id] = (
                    f"Capped at {cap['percent']}% of taxable income = "
                    f"Rs {limit:,.0f}. Claimed Rs {amount:,.0f} exceeds cap."
                )
            else:
                capped[cat_id] = amount
        else:
            capped[cat_id] = amount
    return capped, cap_notes


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def estimate_saving(
    claims: Iterable[Any],
    income_lkr,
    slabs: list[tuple[Decimal | None, Decimal]] | None = None,
) -> dict[str, Any]:
    """Compute deduction total + tax saving for a list of DeductionClaim rows.

    `claims` may be: SQLAlchemy DeductionClaim instances, OR plain dicts with
    keys {category_id, claimed (bool), estimated_lkr, actual_lkr}, OR plain
    tuples (category_id, lkr). The function tolerates all three forms.

    `income_lkr` is the customer's total income before deductions.
    """
    income = _as_decimal(income_lkr)
    caps = get_caps()
    use_slabs = slabs if slabs is not None else DEFAULT_PERSONAL_SLABS_LKR

    # 1. Coerce claims into (category_id -> amount_lkr) — only claimed ones.
    by_cat: dict[str, Decimal] = {}
    for c in claims:
        # Tuple form
        if isinstance(c, tuple) and len(c) == 2:
            cat_id, amount = c
            by_cat[cat_id] = by_cat.get(cat_id, Decimal("0")) + _as_decimal(amount)
            continue
        # Dict form
        if isinstance(c, dict):
            if c.get("claimed") is False:
                continue
            cat_id = c.get("category_id")
            amount = c.get("actual_lkr") if c.get("actual_lkr") is not None else c.get("estimated_lkr")
            if cat_id is None or amount is None:
                continue
            by_cat[cat_id] = by_cat.get(cat_id, Decimal("0")) + _as_decimal(amount)
            continue
        # ORM form
        claimed = getattr(c, "claimed", True)
        if not claimed:
            continue
        cat_id = getattr(c, "category_id", None)
        amount = getattr(c, "actual_lkr", None)
        if amount is None:
            amount = getattr(c, "estimated_lkr", None)
        if cat_id is None or amount is None:
            continue
        by_cat[cat_id] = by_cat.get(cat_id, Decimal("0")) + _as_decimal(amount)

    # 2. Apply category-specific caps.
    capped, cap_notes = _apply_caps(by_cat, income, caps)

    # 3. Total — cap at gross income (cannot deduct more than you earned).
    raw_total = sum(capped.values(), Decimal("0"))
    deduction_cap_applied: str | None = None
    if raw_total > income:
        total = income
        deduction_cap_applied = (
            "Total deductions capped at gross income — you cannot "
            "deduct more than you earned in the year."
        )
    else:
        total = raw_total

    # 4. Saving = tax_before - tax_after.
    tax_before = income_tax_for(income, slabs=use_slabs)
    tax_after = income_tax_for(max(Decimal("0"), income - total), slabs=use_slabs)
    saving = _round_money(tax_before - tax_after)

    # 5. Marginal rate at PRE-deduction income (used by the per-card hint).
    marginal = marginal_rate_for_income(income, slabs=use_slabs)

    # 6. Breakdown.
    breakdown = []
    for cat_id, amount in by_cat.items():
        post_cap = capped.get(cat_id, amount)
        breakdown.append({
            "category_id": cat_id,
            "claimed_lkr": _round_money(amount),
            "after_cap_lkr": _round_money(post_cap),
            "saving_lkr": _round_money(post_cap * marginal),
            "cap_note": cap_notes.get(cat_id),
        })

    return {
        "total_deduction_lkr": _round_money(total),
        "deduction_cap_applied": deduction_cap_applied,
        "marginal_rate": marginal,
        "estimated_saving_lkr": saving,
        "breakdown": breakdown,
        "income_lkr": _round_money(income),
        "tax_before_lkr": tax_before,
        "tax_after_lkr": tax_after,
    }


# ---------------------------------------------------------------------------
# Per-card "if you claim around X, you save Y" — for the catalog cards.
# ---------------------------------------------------------------------------
def per_card_saving_range(
    income_lkr,
    typical_range_lkr: list[int],
    slabs: list[tuple[Decimal | None, Decimal]] | None = None,
) -> dict[str, Decimal]:
    """Convert the catalog `typical_lkr_range` into a saving range at the
    customer's marginal rate.

    Returns: {"low_saving_lkr": Decimal, "high_saving_lkr": Decimal, "marginal_rate": Decimal}
    """
    marginal = marginal_rate_for_income(income_lkr, slabs=slabs)
    low = _as_decimal(typical_range_lkr[0]) if typical_range_lkr else Decimal("0")
    high = _as_decimal(typical_range_lkr[1]) if len(typical_range_lkr) > 1 else low
    return {
        "low_saving_lkr": _round_money(low * marginal),
        "high_saving_lkr": _round_money(high * marginal),
        "marginal_rate": marginal,
    }
