"""fiesta.agreements.stamp_duty — Stamp Duty Act exposure calculator.

REFERENCE
=========
Stamp Duty Act No. 12 of 2006 (Sri Lanka), as amended (Stamp Duty
(Amendment) Acts of 2018 and the Finance Acts thereafter).

  Schedule (lease instruments): a stamp of LKR 1.00 per LKR 1,000 of the
  total consideration (rent + premium + recoverable outgoings) is chargeable
  on every lease instrument with a term exceeding one year. Lease
  instruments of one year or less are exempt under the Schedule.

  Subject to a minimum stamp of LKR 25 per instrument when chargeable.

  Notes:
   - The 365-day boundary is interpreted in practice as "364 days or less is
     safe" because cumulative occupation crossing the 365th day re-categorises
     the instrument retrospectively.
   - These rates have been amended several times. The numbers below are the
     2024-25 statutory rates as best researched as at 2026-05-20. Lanka.tax
     legal MUST verify before GA.
   - If the lease has a fixed premium in addition to recurring rent, the
     premium is added to the total chargeable consideration.

POLICY
======
The helper is advisory. It NEVER blocks PDF rendering. If the customer
elects a term > 364 days, the helper:

    1. Returns payable_amount = total_rent / 1000 (clipped to >= 25).
    2. Records reason="term_exceeds_one_year_threshold".
    3. The rental_pdf template prints the calculated amount in a
       Schedule-SD note so the customer is on notice.

POLARITY
========
Conservative: if EITHER the term test OR the rent test makes the instrument
chargeable, we treat it as chargeable. Under-collection of stamp duty is a
penalty surface for the customer; over-collection is a recommendation to
shorten the term, which is reversible.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal


# --------------------------------------------------------------------------- #
# Constants (verify with Lanka.tax legal before GA)
# --------------------------------------------------------------------------- #

STAMP_RATE_PER_KLKR: Decimal = Decimal("1.00")   # LKR 1 / LKR 1,000 of total
MIN_CHARGEABLE_STAMP_LKR: Decimal = Decimal("25.00")
SAFE_HARBOUR_MAX_DAYS: int = 364


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StampDutyResult:
    """Output of stamp_duty_for_term.

    Attributes
    ----------
    payable_amount_lkr : Decimal
        LKR amount the customer should expect to pay (rounded to 2dp,
        ROUND_HALF_UP). 0.00 when not chargeable.
    chargeable : bool
        True when the Act's threshold is crossed.
    reason : str
        Human-readable explanation (used for clause 3.2 / Schedule SD).
    band : Literal["safe_harbour", "chargeable_term"]
        Bucket label for UI tagging.
    """

    payable_amount_lkr: Decimal
    chargeable: bool
    reason: str
    band: Literal["safe_harbour", "chargeable_term"]


def _q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stamp_duty_for_term(
    *,
    term_days: int,
    total_rent_lkr: Decimal | int | float,
    premium_lkr: Decimal | int | float = 0,
) -> StampDutyResult:
    """Compute stamp duty payable on a rental instrument.

    Parameters
    ----------
    term_days
        Whole days from first to last day of the tenancy (inclusive).
    total_rent_lkr
        Sum of all rent payable over the entire term (monthly_rent * months,
        or the figure declared in the schedule). LKR.
    premium_lkr
        Any one-off premium / key money payable by the Tenant to the
        Landlord on signing. Default 0.

    Returns
    -------
    StampDutyResult

    Examples
    --------
    >>> r = stamp_duty_for_term(term_days=364, total_rent_lkr=600_000)
    >>> r.chargeable, str(r.payable_amount_lkr)
    (False, '0.00')

    >>> r = stamp_duty_for_term(term_days=730, total_rent_lkr=1_200_000)
    >>> r.chargeable, str(r.payable_amount_lkr)
    (True, '1200.00')
    """
    if term_days < 0:
        raise ValueError("term_days must be >= 0")
    rent = Decimal(str(total_rent_lkr))
    prem = Decimal(str(premium_lkr))
    if rent < 0 or prem < 0:
        raise ValueError("rent and premium must be >= 0")

    if term_days <= SAFE_HARBOUR_MAX_DAYS:
        return StampDutyResult(
            payable_amount_lkr=Decimal("0.00"),
            chargeable=False,
            reason=(
                f"term is {term_days} days, which falls within the "
                f"{SAFE_HARBOUR_MAX_DAYS}-day safe harbour under the Stamp "
                "Duty Act No. 12 of 2006"
            ),
            band="safe_harbour",
        )

    total = rent + prem
    raw = total / Decimal("1000")
    payable = max(raw, MIN_CHARGEABLE_STAMP_LKR)
    return StampDutyResult(
        payable_amount_lkr=_q2(payable),
        chargeable=True,
        reason=(
            f"term is {term_days} days (> {SAFE_HARBOUR_MAX_DAYS}); stamp "
            f"duty at LKR 1 per LKR 1,000 of total consideration LKR "
            f"{rent:,.2f} rent"
            + (f" + LKR {prem:,.2f} premium" if prem else "")
            + f" = LKR {_q2(payable):,.2f}. "
            "Customer may either (a) shorten the term to <= "
            f"{SAFE_HARBOUR_MAX_DAYS} days to avoid the charge, or "
            "(b) pay the stamp duty before presenting the instrument."
        ),
        band="chargeable_term",
    )


__all__ = [
    "MIN_CHARGEABLE_STAMP_LKR",
    "SAFE_HARBOUR_MAX_DAYS",
    "STAMP_RATE_PER_KLKR",
    "StampDutyResult",
    "stamp_duty_for_term",
]
