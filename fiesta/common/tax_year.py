"""fiesta.common.tax_year — canonical Year-of-Assessment (YA) model.

Day-0 P0 FIX (C5, 2026-05-27): the product had FOUR different YA formats
coexisting (audit CUSTOMER_FLOW_AUDIT_2026-05-26 §C5):

    - "2025/26"     — topbar selector dropdown
    - "2025-26"     — /tax-bill/<ya> URL routing (S4)
    - "2026/27"     — /fie/al template (hardcoded via current_sl_tax_year)
    - "2026-27"     — /admin/fiesta-states Markov page

Risk: customer paid for one YA, was billed for another.

This module is the single source of truth for YA selection + formatting.
Every read of "what year is active for this user/request" routes through
`active_tax_year(session)`; every render of "what year string belongs in
this widget" routes through a `TaxYear` method.

----
Why a class (vs. a bag of normalisation helpers)?

The existing `normalise_tax_year_to_s4_format` + `normalise_tax_year_to_s5_format`
helpers (fiesta/tax_bill/aggregator.py) convert IN — they accept any of the
four formats and return ONE of them. That's necessary but not sufficient,
because every consumer still has to remember which OUT format it wants
and call the right helper. The TaxYear object inverts that: convert IN
ONCE at the boundary (via `TaxYear.from_any`) and then convert OUT at
the display layer via explicit methods (`.short_slash()`, `.short_dash()`,
`.long_slash()`, `.long_dash()`).

----
Active-year selection (the "which YA is this request for?" question):

    active_tax_year(session) -> TaxYear

Reads (in order):
  1. session['active_tax_year']     (set by topbar selector POST)
  2. current_sl_tax_year() calendar default (paywall.models)
  3. fallback "2025/26" (defensive — should never hit in prod)

If the resolved year isn't in the engine's supported set (currently
{2025-26, 2024-25}), we clamp to the newest supported year. This is the
same policy the /tax-bill route enforces (see tax_bill/routes.py::
show_tax_bill).

----
Why we DON'T just fix this in templates:

Templates can't import; they can only consume context. So we expose
TaxYear via the inject_fiesta_hub_context() context processor, AND we
keep `current_sl_tax_year` (callable form) for backward compat with
templates that still call it. The new pattern templates should use is:

    {{ active_ty.short_slash() }}   {# "2025/26" #}
    {{ active_ty.short_dash() }}    {# "2025-26" #}

The old `current_sl_tax_year()` is still available; both resolvers now
honour `session['active_tax_year']` so they agree on the answer (before
this fix, `current_sl_tax_year()` in templates returned the calendar
default while routes that read `session['active_tax_year']` returned the
session value — that's where the 4-format chaos crept in).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Supported tax years (mirror of fiesta.tax_bill.routes._SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST).
# Kept here to avoid importing tax_bill into the hot path of the topbar
# context processor (tax_bill imports the engine which pulls pydantic). If
# the engine's supported set ever changes, update BOTH places — they're
# locked in lockstep by tests/tax_year/test_ya_unification.py.
# ---------------------------------------------------------------------------
_SUPPORTED_TAX_YEARS_NEWEST_FIRST = (
    (2025, 2026),
    (2024, 2025),
)


_YYYY_YY_PATTERN = re.compile(r"^(\d{4})/(\d{2})$")    # 2025/26
_YYYY_DASH_YY_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")  # 2025-26
_YYYY_YYYY_PATTERN = re.compile(r"^(\d{4})/(\d{4})$")  # 2025/2026
_YYYY_DASH_YYYY_PATTERN = re.compile(r"^(\d{4})-(\d{4})$")  # 2025-2026


@dataclass(frozen=True, eq=True, order=True)
class TaxYear:
    """Canonical Year-of-Assessment.

    Stored internally as ``(start_year, end_year)`` integer tuple — both
    are full 4-digit years (e.g. (2025, 2026) for YA 2025/26). The four
    display formats are computed on demand by the formatter methods so
    callers can't accidentally store a stringly-typed half-form.
    """

    start_year: int
    end_year: int

    # ----- constructors ----------------------------------------------------

    @classmethod
    def from_any(cls, value: "TaxYear | str | None") -> Optional["TaxYear"]:
        """Coerce ANY of the accepted forms into a TaxYear, or None.

        Accepted inputs:
            "2025/26"   YYYY/YY     short slash
            "2025-26"   YYYY-YY     short dash (S4)
            "2025/2026" YYYY/YYYY   long slash (S5)
            "2025-2026" YYYY-YYYY   long dash
            "25/26"     YY/YY       legacy short — accepted via alias table

        Returns None if the input is unrecognised. This is the ONE place
        the four-format chaos collapses into a single canonical shape.
        """
        if value is None:
            return None
        if isinstance(value, TaxYear):
            return value
        s = str(value).strip()
        if not s:
            return None

        # Legacy 2-digit form ("25/26", "25-26", "25_26")
        for sep in ("/", "-", "_"):
            if sep in s and len(s) == 5:
                a, b = s.split(sep, 1)
                if len(a) == 2 and len(b) == 2 and a.isdigit() and b.isdigit():
                    # Assume 21st century (matches existing alias tables).
                    start_year = 2000 + int(a)
                    end_year = 2000 + int(b)
                    if end_year == start_year + 1:
                        return cls(start_year, end_year)

        for pat, full_end in (
            (_YYYY_YY_PATTERN, False),
            (_YYYY_DASH_YY_PATTERN, False),
            (_YYYY_YYYY_PATTERN, True),
            (_YYYY_DASH_YYYY_PATTERN, True),
        ):
            m = pat.match(s)
            if not m:
                continue
            start_year = int(m.group(1))
            if full_end:
                end_year = int(m.group(2))
            else:
                # Build end from start: 2025 + 1 = 2026; the trailing "26"
                # in the input must agree with this.
                end_year = start_year + 1
                if int(m.group(2)) != end_year % 100:
                    # E.g. "2025/27" — internally inconsistent; reject.
                    return None
            if end_year != start_year + 1:
                # E.g. "2025/2027" — not a valid YA.
                return None
            return cls(start_year, end_year)

        return None

    @classmethod
    def calendar_default(cls, today=None) -> "TaxYear":
        """The SL tax year that contains the given date (or today).

        SL fiscal year runs 1 Apr → 31 Mar. Mirrors
        fiesta.paywall.models.current_sl_tax_year, but returns the
        canonical object instead of a string.
        """
        from datetime import date as _date
        today = today or _date.today()
        start_year = today.year if today.month >= 4 else today.year - 1
        return cls(start_year, start_year + 1)

    # ----- formatters ------------------------------------------------------

    def short_slash(self) -> str:
        """YYYY/YY  e.g. '2025/26'  — used by topbar dropdown + counter."""
        return f"{self.start_year}/{str(self.end_year)[-2:]}"

    def short_dash(self) -> str:
        """YYYY-YY  e.g. '2025-26'  — used by /tax-bill/<ya> URLs (S4)."""
        return f"{self.start_year}-{str(self.end_year)[-2:]}"

    def long_slash(self) -> str:
        """YYYY/YYYY e.g. '2025/2026' — used by Submission table, S5 deductions."""
        return f"{self.start_year}/{self.end_year}"

    def long_dash(self) -> str:
        """YYYY-YYYY e.g. '2025-2026' — used by legacy admin Markov page."""
        return f"{self.start_year}-{self.end_year}"

    def __str__(self) -> str:  # pragma: no cover - debug only
        return self.short_slash()


# ---------------------------------------------------------------------------
# Supported-years helpers
# ---------------------------------------------------------------------------

def supported_tax_years() -> list[TaxYear]:
    """Return the list of TaxYear values the engine currently supports,
    newest first. Mirrors _SUPPORTED_TAX_YEARS_S4_NEWEST_FIRST in
    fiesta/tax_bill/routes.py — locked by tests/tax_year/test_ya_unification.py.
    """
    return [TaxYear(s, e) for (s, e) in _SUPPORTED_TAX_YEARS_NEWEST_FIRST]


def _newest_supported() -> TaxYear:
    return supported_tax_years()[0]


# ---------------------------------------------------------------------------
# Active-year resolver — the SINGLE source of "which YA is this request for?"
# ---------------------------------------------------------------------------

def active_tax_year(session=None, *, clamp_to_supported: bool = True) -> TaxYear:
    """Return the active YA for the current request.

    Resolution order:
      1. session['active_tax_year']  (set by topbar selector)
      2. Calendar default            (current SL fiscal year)

    If the resolved year is NOT in the engine's supported set (e.g.
    calendar rolls to 2026/27 before the IRD publishes 26/27 brackets),
    we clamp to the newest supported year. Set clamp_to_supported=False
    if you intentionally want the unclamped calendar default (the
    /admin/fiesta-states page does — it should report the real calendar
    year even when the engine can't compute against it yet).
    """
    candidate: Optional[TaxYear] = None
    if session is not None:
        try:
            raw = session.get("active_tax_year")
        except Exception:
            raw = None
        if raw:
            candidate = TaxYear.from_any(raw)

    if candidate is None:
        candidate = TaxYear.calendar_default()

    if clamp_to_supported:
        supported = supported_tax_years()
        if candidate not in supported:
            logger.debug(
                "active_tax_year: candidate %s not in supported set %s; clamping to %s",
                candidate, supported, _newest_supported(),
            )
            candidate = _newest_supported()

    return candidate


__all__ = [
    "TaxYear",
    "active_tax_year",
    "supported_tax_years",
]
