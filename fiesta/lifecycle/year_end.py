"""fiesta.lifecycle.year_end — X3 year-end transition model + helpers.

Sri Lanka tax year (Year of Assessment, YoA) per IRA No. 24 of 2017 s.20:
  - Runs 1 April -> 31 March.
  - Labelled by the two calendar years it spans (e.g. "2025/26" = 1 Apr 2025
    -> 31 Mar 2026).
  - Statutory filing deadline for individual returns is 30 November
    immediately following the year-end (IRA s.93 read with s.90).

Why a module rather than constants:
  - Customers ask "what tax year am I in?" -> resolution depends on a wall-
    clock instant in SL local time (UTC+5:30).
  - "When does my filing window close?" -> 30 Nov FOLLOWING year-end, not in
    the calendar year of year-start. Off-by-one bait. Encapsulate it.
  - Transition is not just a label flip — it preserves Service Providers,
    Rental Agreements (auto-renew prompt), bank details, persona. All of
    that needs a single owner.

Timezone discipline (per CEO standing rule):
  - All persisted timestamps are UTC.
  - All boundary calculations (1 Apr, 31 Mar, 30 Nov) treat 00:00:00 in
    Asia/Colombo (UTC+5:30, no DST) as the boundary instant.
  - Date comparisons here normalise to SL local before deciding which YoA
    applies — a 1 Apr UTC+0 instant would still be 31 Mar 18:30 in Colombo,
    so the customer is still in the prior tax year.

Public surface (re-exported via fiesta.lifecycle):
  - TaxYear (Pydantic v2 model)
  - TransitionResult (Pydantic v2 model)
  - current_tax_year(now=None) -> TaxYear
  - filing_window_status(filed: bool, return_filed_at, ty, now) -> Literal
  - parse_year_label("2025/26") -> TaxYear
  - transition_customer_to_new_year(customer, current_ty, new_ty, ...) ->
        TransitionResult

The Customer / ServiceProvider / RentalAgreement / BankDetail shapes are
duck-typed protocols — fiesta.lifecycle does not import from the flat-
layout repo root, so this module remains pure-Pydantic and testable in
isolation. The real wiring lives in lifecycle/rollover_scheduler.py
where a Celery task does the SQLAlchemy reads.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sri Lanka standard time. No DST, UTC+5:30 year-round.
SL_TZ = timezone(timedelta(hours=5, minutes=30))

#: First valid Year of Assessment we model. PIT slabs for 24/25 onward live
#: in fiesta.tax; earlier years use legacy SF flow and aren't FIESTA scope.
EARLIEST_YOA = "2024/25"

#: Latest Year of Assessment we'll auto-create. Bumped each year by hand-
#: edit of this constant + a passing regression test (test_x3_s11.py).
#: Keeps a forward-rolling default without surprising customers if FIESTA
#: is left dormant past a YoA boundary.
LATEST_AUTOCREATE_YOA = "2030/31"

#: Regex for canonical year label. Accepts both "2025/26" (short, preferred
#: in UI / persisted strings) and "2025/2026" (long, used in T10 schema and
#: SF EmailTemplate names — preserve for round-trip compatibility).
YEAR_LABEL_SHORT = re.compile(r"^(\d{4})/(\d{2})$")
YEAR_LABEL_LONG = re.compile(r"^(\d{4})/(\d{4})$")

#: Tax year boundary months/days, hard-coded to SL statutory dates.
YEAR_START_MONTH, YEAR_START_DAY = 4, 1     # 1 April
YEAR_END_MONTH, YEAR_END_DAY = 3, 31        # 31 March
FILING_DEADLINE_MONTH, FILING_DEADLINE_DAY = 11, 30  # 30 November

#: How many days before filing-window close to flip status -> "closing_soon".
#: Council brief X3: 30-day pre-deadline reminder. Match it here so UI and
#: scheduler agree.
CLOSING_SOON_DAYS = 30


# ---------------------------------------------------------------------------
# Duck-typed protocols — keep this module decoupled from repo-root models.
# Tests pass plain dicts/objects with these attrs; production wires real
# SQLAlchemy rows.
# ---------------------------------------------------------------------------


class _CustomerLike(Protocol):
    id: int
    persona: Optional[str]  # "salaried" / "rental" / "business" / "mixed"


class _CarryOverLike(Protocol):
    """Common shape of service-provider / rental-agreement / bank-detail
    entries we propagate across years."""

    id: int
    name: str


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TaxYear(BaseModel):
    """A single Sri Lankan Year of Assessment.

    Constructed via parse_year_label("2025/26") or current_tax_year(). All
    derived dates (start_date, end_date, filing_window_close) are computed
    in __init__-time validators so external callers can treat the model as
    immutable and cheap to compare.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    year_label: str = Field(
        ...,
        description="Canonical short label, e.g. '2025/26'. parse_year_label "
        "normalises long-form '2025/2026' input to short.",
    )
    start_date: date = Field(..., description="1 April of starting year, SL local.")
    end_date: date = Field(..., description="31 March of following year, SL local.")
    filing_window_close: date = Field(
        ...,
        description="30 November in the calendar year AFTER end_date. "
        "Per IRA s.93 — 8 months after YoA end.",
    )
    is_active: bool = Field(
        default=False,
        description="True when an instant `now` falls in [start_date, end_date]. "
        "Set by current_tax_year; do not set manually.",
    )

    @field_validator("year_label")
    @classmethod
    def _canonical_label(cls, v: str) -> str:
        v = v.strip()
        m_short = YEAR_LABEL_SHORT.match(v)
        if m_short:
            start = m_short.group(1)
            short_end = m_short.group(2)
            # Sequentiality check: short label's two digits must be
            # start[2:] + 1 (mod 100). e.g. 2025/26 ok, 2025/27 not.
            expected = (int(start) + 1) % 100
            if int(short_end) == expected:
                return v
            raise ValueError(
                f"year_label short form {v!r} not sequential — expected "
                f"'{start}/{expected:02d}'"
            )
        m_long = YEAR_LABEL_LONG.match(v)
        if m_long:
            start, end = m_long.group(1), m_long.group(2)
            # Long-form must be sequential calendar years (2025/2026 ok,
            # 2025/2027 not). Normalise to short.
            if int(end) - int(start) == 1:
                return f"{start}/{end[-2:]}"
        raise ValueError(
            f"year_label must match 'YYYY/YY' or sequential 'YYYY/YYYY', got {v!r}"
        )

    @property
    def short_label(self) -> str:
        """Alias for year_label — explicit at the call site."""
        return self.year_label

    @property
    def long_label(self) -> str:
        """e.g. '2025/2026' — used for T10 round-trip + SF template naming."""
        m = YEAR_LABEL_SHORT.match(self.year_label)
        assert m  # validator guarantees
        start = m.group(1)
        end_short = m.group(2)
        end_full = str(int(start[:2] + end_short))
        return f"{start}/{end_full}"

    @property
    def display_label(self) -> str:
        """Customer-facing string per FIESTA UI standard."""
        return f"Tax year {self.year_label}"


class TransitionResult(BaseModel):
    """Outcome of moving a customer from one YoA to the next.

    `blocked` = True when prior-year obligations remain open (e.g. unfiled
    return). The UI surfaces blockers as a single "file your 25/26 return
    first" CTA rather than allowing parallel-year drift.
    """

    model_config = ConfigDict()

    customer_id: int
    from_year: str
    to_year: str
    blocked: bool = False
    blocker_reasons: list[str] = Field(default_factory=list)
    carried_over: dict[str, int] = Field(
        default_factory=dict,
        description="Map of resource_type -> count carried forward. "
        "Keys: service_providers, rental_agreements, bank_details, persona, "
        "company_expenses (recurring only).",
    )
    new_tax_file_created: bool = False
    auto_renewals_pending: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Rental agreements / SP contracts whose end_date falls "
        "in the OLD year and need an explicit auto-renew Y/N from the user.",
    )
    transitioned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def ok(self) -> bool:
        return (not self.blocked) and self.new_tax_file_created


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_sl_local(dt: Optional[datetime]) -> datetime:
    """Convert any aware datetime (or naive-assumed-UTC) to SL local."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SL_TZ)


def _build_tax_year(start_year: int) -> TaxYear:
    """Build a TaxYear from the starting calendar year (e.g. 2025 -> 25/26)."""
    label = f"{start_year}/{str(start_year + 1)[-2:]}"
    start = date(start_year, YEAR_START_MONTH, YEAR_START_DAY)
    end = date(start_year + 1, YEAR_END_MONTH, YEAR_END_DAY)
    # Filing window closes 30 Nov in the calendar year of YoA end (8 months
    # after year-end). For 25/26 (ends 31 Mar 2026) -> closes 30 Nov 2026.
    filing_close = date(start_year + 1, FILING_DEADLINE_MONTH, FILING_DEADLINE_DAY)
    return TaxYear(
        year_label=label,
        start_date=start,
        end_date=end,
        filing_window_close=filing_close,
    )


def parse_year_label(label: str) -> TaxYear:
    """Parse '2025/26' or '2025/2026' -> TaxYear.

    Raises ValueError on malformed input. Used by callers that already have
    a label (e.g. URL param, stored field) and need the boundary dates.
    """
    label = label.strip()
    m_short = YEAR_LABEL_SHORT.match(label)
    if m_short:
        start = int(m_short.group(1))
        short_end = int(m_short.group(2))
        expected = (start + 1) % 100
        if short_end == expected:
            return _build_tax_year(start)
        raise ValueError(
            f"year_label short form {label!r} not sequential — expected "
            f"'{start}/{expected:02d}'"
        )
    m_long = YEAR_LABEL_LONG.match(label)
    if m_long and int(m_long.group(2)) - int(m_long.group(1)) == 1:
        return _build_tax_year(int(m_long.group(1)))
    raise ValueError(f"unparseable year_label: {label!r}")


def current_tax_year(now: Optional[datetime] = None) -> TaxYear:
    """Which Year of Assessment is active right now in Sri Lanka?

    Boundary semantics (matters at 31 Mar / 1 Apr midnight):
      - If now (SL local) is BEFORE 1 Apr -> we're in the YoA that started
        on the PREVIOUS 1 Apr.
      - If now (SL local) is ON OR AFTER 1 Apr -> new YoA has begun.

    Returns a TaxYear with is_active=True.
    """
    sl_now = _to_sl_local(now)
    if (sl_now.month, sl_now.day) >= (YEAR_START_MONTH, YEAR_START_DAY):
        start_year = sl_now.year
    else:
        start_year = sl_now.year - 1
    ty = _build_tax_year(start_year)
    return ty.model_copy(update={"is_active": True})


def filing_window_status(
    filed: bool,
    return_filed_at: Optional[datetime],
    ty: TaxYear,
    now: Optional[datetime] = None,
) -> Literal["open", "closing_soon", "overdue", "filed"]:
    """What's the filing state of `ty` for a single customer?

    - "filed"        — Return_Filed__c equivalent is True (or filed_at set).
    - "open"         — we're inside the window, > CLOSING_SOON_DAYS to go.
    - "closing_soon" — we're inside the window, <= CLOSING_SOON_DAYS to go.
    - "overdue"      — past filing_window_close, still unfiled.

    The window is considered "open" once the YoA has ended (1 Apr year+1
    onwards). Filing during the YoA itself is unusual; we still surface
    "open" so the customer isn't told "you can't file yet" when they can
    (early filers who lodged their return during the year, e.g. expats
    leaving the country mid-year).
    """
    if filed or return_filed_at is not None:
        return "filed"

    sl_today = _to_sl_local(now).date()
    days_to_close = (ty.filing_window_close - sl_today).days

    if days_to_close < 0:
        return "overdue"
    if days_to_close <= CLOSING_SOON_DAYS:
        return "closing_soon"
    return "open"


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


def transition_customer_to_new_year(
    customer: _CustomerLike,
    current_ty: TaxYear,
    new_ty: TaxYear,
    *,
    prior_year_filed: bool,
    service_providers: Iterable[_CarryOverLike] = (),
    rental_agreements: Iterable[Any] = (),
    bank_details: Iterable[_CarryOverLike] = (),
    recurring_expenses: Iterable[_CarryOverLike] = (),
    now: Optional[datetime] = None,
) -> TransitionResult:
    """Move a customer from current_ty -> new_ty.

    Pre-conditions enforced (block if violated):
      1. new_ty must start strictly after current_ty.
      2. The prior-year return must be either filed OR within the open
         filing window. We allow transition with an unfiled return so the
         customer can work on both years in parallel (some clients file
         late while already accumulating new-year data), BUT we mark the
         result as 'blocked' if their prior-year return is OVERDUE so the
         UI can route them to a "file prior year first" remediation flow.

    Carry-over rules:
      - service_providers: all active SPs carry over. Auto-renew prompt
        for any whose contract_end_date falls inside current_ty.end_date
        window (caller passes any object with optional `contract_end_date`).
      - rental_agreements: 364-day fixed-term agreements (the SL standard
        commercial-lease pattern) typically renew. We surface them for
        explicit user Y/N rather than auto-creating to avoid silently
        committing the customer to a renewal they didn't agree to.
      - bank_details: copied without prompt — accounts don't change at the
        tax-year boundary.
      - persona: copied — persona changes are explicit user actions, not
        annual events.
      - recurring_expenses: monthly/quarterly subscription expenses copy.
        One-off expenses do NOT (caller filters before passing).

    Side-effect: returns a TransitionResult describing what WOULD happen.
    The actual SQLAlchemy writes are done by rollover_scheduler so this
    function stays pure and testable.
    """
    result = TransitionResult(
        customer_id=customer.id,
        from_year=current_ty.year_label,
        to_year=new_ty.year_label,
    )

    # --- Pre-condition 1: ordering -----------------------------------------
    if new_ty.start_date <= current_ty.start_date:
        result.blocked = True
        result.blocker_reasons.append(
            f"new_year {new_ty.year_label} must start after current "
            f"{current_ty.year_label}"
        )
        return result

    # --- Pre-condition 2: prior-year filing status -------------------------
    prior_status = filing_window_status(
        filed=prior_year_filed,
        return_filed_at=None,
        ty=current_ty,
        now=now,
    )
    if prior_status == "overdue":
        result.blocked = True
        result.blocker_reasons.append(
            f"prior year {current_ty.year_label} return is overdue — "
            "file it before transitioning"
        )
        # Don't return early; surface what WOULD have carried over so the
        # UI can render an informative "blocked but ready to resume" panel.

    # --- Carry-over accounting --------------------------------------------
    sps = list(service_providers)
    rentals = list(rental_agreements)
    banks = list(bank_details)
    recurring = list(recurring_expenses)

    result.carried_over["service_providers"] = len(sps)
    result.carried_over["rental_agreements"] = len(rentals)
    result.carried_over["bank_details"] = len(banks)
    result.carried_over["recurring_expenses"] = len(recurring)
    result.carried_over["persona"] = 1 if customer.persona else 0

    # --- Auto-renew prompts -----------------------------------------------
    for r in rentals:
        end = getattr(r, "contract_end_date", None) or getattr(r, "end_date", None)
        if end is not None and current_ty.start_date <= end <= current_ty.end_date:
            result.auto_renewals_pending.append(
                {
                    "type": "rental_agreement",
                    "id": getattr(r, "id", None),
                    "name": getattr(r, "name", None) or getattr(r, "address", None),
                    "ends_on": end.isoformat(),
                    "default_action": "prompt",
                }
            )

    for sp in sps:
        end = getattr(sp, "contract_end_date", None)
        if end is not None and current_ty.start_date <= end <= current_ty.end_date:
            result.auto_renewals_pending.append(
                {
                    "type": "service_provider",
                    "id": getattr(sp, "id", None),
                    "name": getattr(sp, "name", None),
                    "ends_on": end.isoformat(),
                    "default_action": "prompt",
                }
            )

    # --- Mark the (would-be) write -----------------------------------------
    # Even when blocked, we still set new_tax_file_created=False so the
    # caller can decide whether to override (CEO admin operation).
    result.new_tax_file_created = not result.blocked
    return result


__all__ = [
    "SL_TZ",
    "EARLIEST_YOA",
    "LATEST_AUTOCREATE_YOA",
    "CLOSING_SOON_DAYS",
    "TaxYear",
    "TransitionResult",
    "current_tax_year",
    "filing_window_status",
    "parse_year_label",
    "transition_customer_to_new_year",
]
