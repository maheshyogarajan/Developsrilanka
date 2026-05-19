"""fiesta.lifecycle.invoice_cadence — S11 invoice cadence tracking.

Why this module exists:
  - Service providers + rental landlords + recurring business expenses
    generate invoices on a regular cadence (monthly, quarterly, annual).
  - IRD audits expect proportional coverage: 12 months of rent receipts
    for a 12-month rental, monthly retainer invoices to match monthly
    salary deductions, etc.
  - Gaps = audit risk. Cadence irregularity = audit risk. FIESTA's job is
    to surface both before the customer signs off their return.
  - This feeds X6 compliance gates on the S6 service-providers screen:
    if a SP is below market-rate AND cadence is irregular, the X6 gate
    can refuse to allow the customer to claim the deduction until they
    explain the gap.

Detection strategy:
  - Expected cadence is either declared by the customer (per-SP
    `expected_cadence` field) or auto-inferred by clustering interval
    histograms. We persist whichever wins.
  - Coefficient of variation (CV) of intervals between invoices is the
    irregularity signal. CV = stddev / mean. <= 0.15 = regular,
    <= 0.30 = mildly irregular, > 0.30 = irregular -> flag.
  - "Missing periods" are computed against expected cadence + start/end
    of the relationship (or the active tax year, whichever is narrower).

S11 -> X6 hand-off (per council brief):
  - CadenceCheck.irregular_flag=True OR coverage_gaps_count > 0 surfaces
    in the SP record. fiesta.compliance.market_rates_table compares
    invoice amount to market rate. The two signals jointly drive an
    X6 challenge ("explain this SP — irregular AND above market").
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Coefficient of variation thresholds for cadence regularity.
CV_REGULAR_MAX = 0.15
CV_MILD_MAX = 0.30

#: Pre-period reminder lead time. 5 days mirrors typical SL billing-cycle
#: lead time (most service providers issue invoices 3-7 days before period
#: end so payment lands inside the period).
INVOICE_REMINDER_LEAD_DAYS = 5

#: Cadence label -> nominal period in days. Used for missing-period
#: detection. Real-world variance is absorbed by the CV calculation.
CADENCE_PERIOD_DAYS = {
    "monthly": 30,
    "quarterly": 91,  # 3 * ~30.4
    "biannual": 182,
    "annual": 365,
}

#: Invoice statuses we surface in the UI.
InvoiceStatus = Literal["issued", "paid", "pending"]

#: IRA categorization buckets — these align with fiesta.tax.types.Income
#: components + fiesta.compliance bucket names.
IRACategory = Literal[
    "employment_deduction",
    "rental_expense",
    "business_expense",
    "qualifying_payment",
    "personal_expense",  # not deductible — tracked for completeness
    "other",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Invoice(BaseModel):
    """A single invoice attached to a (customer, service_provider) pair.

    Decimal for monetary amounts. Dates are timezone-naive (calendar dates,
    not instants — SL local).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Primary key — auto-assigned by app DB.")
    customer_id: int
    service_provider_id: Optional[int] = Field(
        default=None,
        description="FK -> service_provider. None for ad-hoc invoices "
        "(rare; flagged as 'unattached' in audit).",
    )
    invoice_date: date = Field(..., description="Date of issue.")
    period_start: date = Field(
        ...,
        description="Coverage period start (e.g. 1 May for May rent). For "
        "one-off invoices, equals invoice_date.",
    )
    period_end: date = Field(..., description="Coverage period end.")
    amount_lkr: Decimal = Field(
        ...,
        description="Amount in LKR. Foreign-currency invoices store both "
        "amount_foreign + amount_lkr (converted at invoice_date FX rate).",
    )
    amount_foreign: Optional[Decimal] = Field(default=None)
    currency: str = Field(
        default="LKR",
        description="ISO-4217 code. 'LKR' default; foreign values trigger "
        "FX conversion at amount_lkr persistence time (out of scope here).",
    )
    status: InvoiceStatus = Field(default="issued")
    ira_categorization: IRACategory = Field(default="other")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @field_validator("period_end")
    @classmethod
    def _period_order(cls, v: date, info) -> date:
        ps = info.data.get("period_start")
        if ps is not None and v < ps:
            raise ValueError("period_end must be >= period_start")
        return v


class CadenceCheck(BaseModel):
    """Result of a per-(customer, SP) cadence detection pass."""

    model_config = ConfigDict()

    customer_id: int
    sp_id: Optional[int]
    expected_cadence: Optional[
        Literal["monthly", "quarterly", "biannual", "annual"]
    ] = None
    actual_cadence: Optional[
        Literal["monthly", "quarterly", "biannual", "annual", "irregular"]
    ] = None
    invoice_count: int = 0
    coefficient_of_variation: Optional[float] = None
    irregular_flag: bool = False
    coverage_gaps: list[dict] = Field(
        default_factory=list,
        description="Each gap: {'expected_period_start','expected_period_end',"
        "'missed_count'}. Adjacent missing periods may be coalesced.",
    )
    mean_amount_lkr: Optional[Decimal] = None
    last_invoice_date: Optional[date] = None
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field  # type: ignore[misc]
    @property
    def coverage_gaps_count(self) -> int:
        return sum(int(g.get("missed_count", 0)) for g in self.coverage_gaps)

    @computed_field  # type: ignore[misc]
    @property
    def cadence_consistent(self) -> bool:
        if self.expected_cadence is None or self.actual_cadence is None:
            return False
        return self.expected_cadence == self.actual_cadence


class ReminderTrigger(BaseModel):
    """Output of upcoming_invoice_reminder — what to send and when."""

    customer_id: int
    sp_id: Optional[int]
    reminder_kind: Literal[
        "monthly_invoice_due_soon",
        "monthly_invoice_missing",
        "quarterly_cycle_starts",
        "next_due_after_irregular_gap",
    ]
    due_date: date
    message_hint: str
    idempotency_key: str


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_cadence(payments_for_sp: Iterable[Invoice]) -> CadenceCheck:
    """Compute CadenceCheck from a list of invoices for one (customer, SP).

    Algorithm:
      1. Sort by invoice_date ascending.
      2. If < 2 invoices, return a low-confidence check (cadence unknown).
      3. Compute intervals between consecutive invoice_dates.
      4. Compute mean + stddev + CV. Bucket actual_cadence by mean.
      5. Compute coverage gaps: for each consecutive pair where the gap
         is > 1.5x expected period, count the missing periods.
      6. Set irregular_flag if CV > CV_MILD_MAX.
    """
    invs = sorted(payments_for_sp, key=lambda i: i.invoice_date)

    if not invs:
        return CadenceCheck(
            customer_id=0, sp_id=None, invoice_count=0, irregular_flag=False
        )

    customer_id = invs[0].customer_id
    sp_id = invs[0].service_provider_id

    if len(invs) == 1:
        return CadenceCheck(
            customer_id=customer_id,
            sp_id=sp_id,
            invoice_count=1,
            irregular_flag=False,
            mean_amount_lkr=invs[0].amount_lkr,
            last_invoice_date=invs[0].invoice_date,
        )

    # Intervals in days.
    intervals = [
        (invs[i + 1].invoice_date - invs[i].invoice_date).days
        for i in range(len(invs) - 1)
    ]
    mean_days = statistics.mean(intervals)
    stddev_days = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
    cv = stddev_days / mean_days if mean_days > 0 else 0.0

    actual = _bucket_cadence(mean_days, cv)
    expected_period = CADENCE_PERIOD_DAYS.get(actual or "", 30)

    # Coverage gaps: any interval > 1.5 * expected_period -> at least one
    # missed cycle. missed_count = round((interval / expected_period) - 1).
    gaps: list[dict] = []
    for i, gap_days in enumerate(intervals):
        if actual == "irregular":
            # Use the nominal period guess closest to mean_days for gap
            # estimation; otherwise we'd report every cycle as a gap.
            ref_period = max(_nearest_nominal_period(mean_days), 1)
        else:
            ref_period = expected_period
        if gap_days > 1.5 * ref_period:
            missed_count = max(1, round(gap_days / ref_period) - 1)
            gaps.append(
                {
                    "expected_period_start": (
                        invs[i].invoice_date + timedelta(days=ref_period)
                    ).isoformat(),
                    "expected_period_end": (
                        invs[i + 1].invoice_date - timedelta(days=1)
                    ).isoformat(),
                    "missed_count": missed_count,
                }
            )

    mean_amount = Decimal(
        str(round(statistics.mean(float(i.amount_lkr) for i in invs), 2))
    )

    return CadenceCheck(
        customer_id=customer_id,
        sp_id=sp_id,
        actual_cadence=actual,
        invoice_count=len(invs),
        coefficient_of_variation=round(cv, 3),
        irregular_flag=cv > CV_MILD_MAX,
        coverage_gaps=gaps,
        mean_amount_lkr=mean_amount,
        last_invoice_date=invs[-1].invoice_date,
    )


def _bucket_cadence(
    mean_days: float, cv: float
) -> Optional[Literal["monthly", "quarterly", "biannual", "annual", "irregular"]]:
    """Map mean inter-invoice gap to a cadence label."""
    if cv > CV_MILD_MAX:
        return "irregular"
    if mean_days <= 45:
        return "monthly"
    if mean_days <= 120:
        return "quarterly"
    if mean_days <= 220:
        return "biannual"
    if mean_days <= 400:
        return "annual"
    return None  # very long gaps — out of bucket range


def _nearest_nominal_period(mean_days: float) -> int:
    nominal = sorted(CADENCE_PERIOD_DAYS.values())
    return min(nominal, key=lambda d: abs(d - mean_days))


def upcoming_invoice_reminder(
    customer_id: int,
    sp_id: Optional[int],
    check: CadenceCheck,
    today: date,
    *,
    lead_days: int = INVOICE_REMINDER_LEAD_DAYS,
) -> Optional[ReminderTrigger]:
    """Should we send a reminder TODAY to add the next invoice?

    Returns None when no reminder is due, else a ReminderTrigger payload.
    """
    if check.last_invoice_date is None or check.actual_cadence is None:
        return None
    if check.actual_cadence == "irregular":
        # Irregular cadence — emit a "review cadence" hint rather than a
        # specific date-based reminder.
        if check.coverage_gaps_count > 0:
            return ReminderTrigger(
                customer_id=customer_id,
                sp_id=sp_id,
                reminder_kind="next_due_after_irregular_gap",
                due_date=today,
                message_hint=(
                    f"{check.coverage_gaps_count} missing period(s) for "
                    f"this provider. Add missing invoices or document the "
                    f"reason for the gap."
                ),
                idempotency_key=(
                    f"cust{customer_id}:sp{sp_id}:cadence_review:{today.isoformat()}"
                ),
            )
        return None

    period_days = CADENCE_PERIOD_DAYS[check.actual_cadence]
    next_due = check.last_invoice_date + timedelta(days=period_days)
    days_until = (next_due - today).days

    if days_until == lead_days:
        return ReminderTrigger(
            customer_id=customer_id,
            sp_id=sp_id,
            reminder_kind="monthly_invoice_due_soon" if check.actual_cadence == "monthly"
            else ("quarterly_cycle_starts" if check.actual_cadence == "quarterly"
                  else "monthly_invoice_due_soon"),
            due_date=next_due,
            message_hint=(
                f"Next {check.actual_cadence} invoice expected on "
                f"{next_due.isoformat()}."
            ),
            idempotency_key=(
                f"cust{customer_id}:sp{sp_id}:due_soon:{next_due.isoformat()}"
            ),
        )

    # Missed-period escalation: if next_due is in the past (days_until < 0)
    # and the gap exceeds half a period, flag missing.
    if days_until < 0 and abs(days_until) > period_days / 2:
        return ReminderTrigger(
            customer_id=customer_id,
            sp_id=sp_id,
            reminder_kind="monthly_invoice_missing",
            due_date=next_due,
            message_hint=(
                f"Expected {check.actual_cadence} invoice on "
                f"{next_due.isoformat()} hasn't been added yet."
            ),
            idempotency_key=(
                f"cust{customer_id}:sp{sp_id}:missing:{next_due.isoformat()}"
            ),
        )

    return None


# ---------------------------------------------------------------------------
# Above-market-rate gate (S11 -> X6 link)
# ---------------------------------------------------------------------------


def is_above_market_rate(
    avg_invoice_amount_lkr: Decimal,
    market_rate_lkr: Decimal,
    *,
    threshold_ratio: Decimal = Decimal("1.25"),
) -> bool:
    """Pure helper — does the average invoice exceed market by > 25%?

    25% chosen to align with fiesta.compliance.market_rates_table.yaml's
    band tolerance. When True AND cadence is irregular, X6 gate refuses
    silent acceptance of the deduction.
    """
    if market_rate_lkr <= 0:
        return False
    return (avg_invoice_amount_lkr / market_rate_lkr) > threshold_ratio


__all__ = [
    "CV_REGULAR_MAX",
    "CV_MILD_MAX",
    "INVOICE_REMINDER_LEAD_DAYS",
    "CADENCE_PERIOD_DAYS",
    "Invoice",
    "CadenceCheck",
    "ReminderTrigger",
    "detect_cadence",
    "upcoming_invoice_reminder",
    "is_above_market_rate",
]
