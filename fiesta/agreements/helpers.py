"""fiesta.agreements.helpers — shared computation helpers for the Agreement Generator.

B4 (F5.5) — Tax-savings framing on S8 + S9 preview screens.

``compute_protected_deductions_lkr(user, sp_or_property)``
    Returns an integer estimate of the LKR amount a documented agreement
    protects, expressed as:

        annual_expense_lkr * marginal_rate_for_user

    Strategy used (v1 — documented):
    ---------------------------------
    The ideal approach is to call ``fiesta.deductions.estimate.marginal_rate_for_income``
    with the user's declared gross income. In this first cut we apply that
    function when the user object exposes a numeric ``gross_income`` or
    ``annual_income`` attribute; otherwise we fall back to 0.30 (the 5th of
    six personal-income slabs, covering the Rs 3.2M–3.7M band — a conservative
    proxy for a FIESTA-typical foreign-income earner).

    The annual expense is read from the SP or property object in this order:
    - For SPs:        12 × monthly_rate (monthly retainer) OR 0 if unset.
    - For properties: 12 × monthly_rent_lkr × home_office_pct (0–100) / 100.
      If monthly_rent_lkr is unavailable we return 0.

    The return value is rounded to the nearest Rs 1,000 to avoid false
    precision (the underlying income may be stale / unreported).

    Callers that cannot supply a real user object may pass ``user=None``;
    the function then applies the 0.30 proxy.

    Limitations (acknowledged):
    - SPs with an hourly rate only: we cannot infer annual expense without
      hours-worked data, so we return 0. Callers should handle 0 gracefully
      (suppress the framing block rather than showing "Rs 0 protected").
    - The 0.30 proxy understates the saving for top-slab earners (36%).
      A future iteration should read the user's confirmed income from their
      FIESTA triage / profile record.
    - We do NOT model the §195 disclosure uplift (related-party situation may
      mean the effective deductible amount is lower if the IRD challenges
      arm's-length pricing). That complexity is intentionally deferred.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

# Fallback marginal rate when the user's income is not available.
_FALLBACK_MARGINAL_RATE = Decimal("0.30")

# Rounding quantum — nearest Rs 1,000.
_ROUND_TO = Decimal("1000")


def _user_marginal_rate(user) -> Decimal:
    """Resolve the user's marginal rate, falling back to 0.30 if unavailable."""
    if user is None:
        return _FALLBACK_MARGINAL_RATE

    # Try common attribute names for gross / annual income.
    income_raw = None
    for attr in ("gross_income", "annual_income", "income_lkr", "total_income"):
        v = getattr(user, attr, None)
        if v is not None:
            income_raw = v
            break

    if income_raw is None:
        return _FALLBACK_MARGINAL_RATE

    try:
        from fiesta.deductions.estimate import marginal_rate_for_income  # lazy import
        return marginal_rate_for_income(income_raw)
    except Exception as exc:  # noqa: BLE001
        logger.debug("marginal_rate_for_income lookup failed: %s — using fallback", exc)
        return _FALLBACK_MARGINAL_RATE


def _annual_expense_from_sp(sp) -> Decimal:
    """Derive the annual deductible expense from a ServiceProvider object."""
    if sp is None:
        return Decimal("0")

    # Prefer monthly_rate (retainer) — most common FIESTA use case.
    monthly = getattr(sp, "monthly_rate", None)
    if monthly is not None:
        try:
            return Decimal(str(monthly)) * Decimal("12")
        except Exception:  # noqa: BLE001
            pass

    # Hourly rate only — cannot infer annual expense without hours data.
    return Decimal("0")


def _annual_expense_from_property(prop) -> Decimal:
    """Derive the deductible home-office portion from a Property object."""
    if prop is None:
        return Decimal("0")

    monthly_rent = getattr(prop, "monthly_rent_lkr", None)
    if monthly_rent is None:
        return Decimal("0")

    # home_office_percentage stored as 0–100 (e.g. 25.0 = 25%).
    # Fall back to 100% (full rent deductible) if not set.
    pct_raw = getattr(prop, "home_office_percentage", None)
    if pct_raw is None:
        pct_raw = getattr(prop, "home_office_pct", None)

    try:
        pct = Decimal(str(pct_raw)) / Decimal("100") if pct_raw is not None else Decimal("1")
        return Decimal(str(monthly_rent)) * Decimal("12") * pct
    except Exception:  # noqa: BLE001
        return Decimal("0")


def compute_protected_deductions_lkr(user, sp_or_property, *, is_property: bool = False) -> int:
    """Return the approximate LKR value protected by a documented agreement.

    Parameters
    ----------
    user:
        Flask-Login ``current_user`` (or any object with an income attribute).
        Pass ``None`` to apply the fallback marginal rate.
    sp_or_property:
        A ServiceProvider ORM instance (``is_property=False``) or a Property
        ORM instance (``is_property=True``).
    is_property:
        Set ``True`` when ``sp_or_property`` is a Property; ``False`` (default)
        for a ServiceProvider.

    Returns
    -------
    int
        Estimated protected deduction in LKR, rounded to the nearest Rs 1,000.
        Returns 0 when the annual expense cannot be determined (e.g. SP with
        hourly-only rate).
    """
    rate = _user_marginal_rate(user)

    if is_property:
        annual_expense = _annual_expense_from_property(sp_or_property)
    else:
        annual_expense = _annual_expense_from_sp(sp_or_property)

    if annual_expense <= Decimal("0"):
        return 0

    raw = annual_expense * rate
    rounded = (raw / _ROUND_TO).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * _ROUND_TO
    return int(rounded)


__all__ = ["compute_protected_deductions_lkr"]
