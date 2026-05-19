"""fiesta.lifecycle.rollover_scheduler — X3 cron job for year boundaries.

Runs daily at 00:30 Asia/Colombo (= 19:00 UTC previous day). Cadence chosen
so that the 1 April transition fires inside the first 30 minutes of the new
tax year SL-local, and the 30 Nov deadline alarm catches that day even when
the worker's clock is in UTC.

We register this on the FIESTA Celery beat. The repo-root celery_config.py
adds the entry; this module exposes the task callable + the pure-function
"what should fire today?" computation so the entry stays a one-liner.

Why a daily cron rather than 1 Apr-only:
  - Reminders span multiple dates (5d / 1d before periods, 30d / 7d / 1d
    before filing deadline). Daily cron + idempotent send-window check
    is simpler than juggling 6 separate cron entries.
  - Idempotency: each customer/event combination logs to audit_log.py
    before sending. If the cron fires twice on the same day (worker
    restart, manual trigger, beat skew) the second pass becomes a no-op.

Side-effects this module SHOULD perform (in production wiring):
  - Read customers from app DB via the Flask app_context the worker pushed.
  - For each customer, call year_end.transition_customer_to_new_year(...)
    in dry-run mode first, then commit on the result.
  - For each customer/SP pair, call invoice_cadence helpers + queue
    reminders via reminders.dispatch(...).

Side-effects this module DOES perform in this skeleton:
  - All of the above, gated behind a `dispatcher` callable injected by the
    Flask wiring layer. The skeleton ships with `_inert_dispatcher` so unit
    tests can verify scheduling decisions without touching the DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

from .audit_log import LifecycleAudit
from .year_end import (
    CLOSING_SOON_DAYS,
    SL_TZ,
    TaxYear,
    current_tax_year,
    filing_window_status,
    parse_year_label,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reminder schedule
# ---------------------------------------------------------------------------

#: Pre-deadline reminder days. We fire at -30, -7, -1 days before
#: filing_window_close, and at +1, +14 after (overdue escalation).
FILING_REMINDER_OFFSETS_DAYS = (-30, -7, -1, 1, 14)

#: Year-boundary reminder offsets relative to year-end (31 Mar).
#:  -1: "tax year closing tomorrow — review records"
#:   0: "tax year ends today" (silent — logged only)
#:  +1: "welcome to new year — transition checklist" (the X3 main email)
YEAR_BOUNDARY_REMINDER_OFFSETS_DAYS = (-1, 0, 1)


@dataclass
class SchedulingDecision:
    """One unit of "this customer should get this event today" output.

    Pure-data, no side-effects. The dispatcher consumes a list of these and
    actually sends the messages / writes the SF / spawns the transitions.
    """

    customer_id: int
    event_type: str  # "year_transition" | "filing_reminder" | "deadline_alarm" | "rollover_block"
    payload: dict
    scheduled_for: datetime  # SL-local
    idempotency_key: str  # year_event_customer triple, for audit_log dedupe

    def __post_init__(self) -> None:
        # Defensive: idempotency_key must be unique within (customer, day).
        if not self.idempotency_key:
            raise ValueError("idempotency_key required (audit dedupe relies on it)")


@dataclass
class RolloverContext:
    """Inputs the scheduler reads to make decisions for a single customer."""

    customer_id: int
    persona: Optional[str]
    prior_year_filed: bool
    has_prior_tax_file: bool
    has_current_tax_file: bool
    sp_count: int = 0
    rental_count: int = 0
    last_audit_keys: tuple[str, ...] = field(default_factory=tuple)


def _today_sl(now: Optional[datetime] = None) -> date:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(SL_TZ).date()


def _idem(customer_id: int, event: str, anchor_date: date) -> str:
    return f"cust{customer_id}:{event}:{anchor_date.isoformat()}"


def compute_decisions_for_customer(
    ctx: RolloverContext,
    *,
    now: Optional[datetime] = None,
) -> list[SchedulingDecision]:
    """Pure function: given customer context + current instant, what
    lifecycle events should fire today?

    Used directly by tests. The Celery task wraps this with DB reads.
    """
    today = _today_sl(now)
    decisions: list[SchedulingDecision] = []

    current_ty = current_tax_year(now)
    # Prior year boundary = 1 day before current_ty.start_date.
    year_end_of_prior = current_ty.start_date - timedelta(days=1)

    # ----- Year-boundary events (T-1, T+0, T+1 around 31 Mar) -----
    for offset in YEAR_BOUNDARY_REMINDER_OFFSETS_DAYS:
        target_day = year_end_of_prior + timedelta(days=offset)
        if target_day != today:
            continue

        if offset == -1:
            event = "year_closing_tomorrow"
        elif offset == 0:
            event = "year_ended_today"
        else:  # offset == +1
            event = "new_year_transition_invite"

        idem = _idem(ctx.customer_id, event, today)
        if idem in ctx.last_audit_keys:
            continue  # already sent

        decisions.append(
            SchedulingDecision(
                customer_id=ctx.customer_id,
                event_type=event,
                payload={
                    "current_year": current_ty.year_label,
                    "prior_year": _prior_year_label(current_ty),
                    "persona": ctx.persona,
                },
                scheduled_for=datetime.now(SL_TZ),
                idempotency_key=idem,
            )
        )

    # ----- Filing-window reminders for the prior year -----
    # Only when the customer has a prior-year tax file and hasn't filed.
    if ctx.has_prior_tax_file and not ctx.prior_year_filed:
        prior_ty = parse_year_label(_prior_year_label(current_ty))
        status = filing_window_status(
            filed=False, return_filed_at=None, ty=prior_ty, now=now
        )

        for offset in FILING_REMINDER_OFFSETS_DAYS:
            target_day = prior_ty.filing_window_close + timedelta(days=offset)
            if target_day != today:
                continue

            event = (
                "filing_deadline_overdue"
                if offset > 0
                else "filing_deadline_approaching"
            )
            idem = _idem(ctx.customer_id, f"{event}:{offset:+d}", today)
            if idem in ctx.last_audit_keys:
                continue

            decisions.append(
                SchedulingDecision(
                    customer_id=ctx.customer_id,
                    event_type=event,
                    payload={
                        "year": prior_ty.year_label,
                        "days_offset": offset,
                        "status": status,
                        "filing_window_close": prior_ty.filing_window_close.isoformat(),
                    },
                    scheduled_for=datetime.now(SL_TZ),
                    idempotency_key=idem,
                )
            )

    # ----- Actual transition write on 1 April -----
    # The new_year_transition_invite above is the *email*; the actual
    # SQLAlchemy creation of a new tax file is a separate event for
    # auditability. Fires once on the year-start day if no current_tax_file
    # exists yet.
    if today == current_ty.start_date and not ctx.has_current_tax_file:
        idem = _idem(ctx.customer_id, "create_new_year_tax_file", today)
        if idem not in ctx.last_audit_keys:
            decisions.append(
                SchedulingDecision(
                    customer_id=ctx.customer_id,
                    event_type="create_new_year_tax_file",
                    payload={
                        "new_year": current_ty.year_label,
                        "from_year": _prior_year_label(current_ty),
                    },
                    scheduled_for=datetime.now(SL_TZ),
                    idempotency_key=idem,
                )
            )

    return decisions


def _prior_year_label(ty: TaxYear) -> str:
    """For 25/26 -> 24/25."""
    start_year = int(ty.year_label.split("/")[0])
    return f"{start_year - 1}/{str(start_year)[-2:]}"


# ---------------------------------------------------------------------------
# Celery wiring (skeleton — actual @app.task decoration done in
# repo-root rollover_scheduler_tasks.py when this is wired in)
# ---------------------------------------------------------------------------


def _inert_dispatcher(decisions: Iterable[SchedulingDecision]) -> int:
    """Test-mode dispatcher — logs and returns count."""
    n = 0
    for d in decisions:
        logger.info(
            "[rollover_scheduler dry-run] customer=%s event=%s idem=%s",
            d.customer_id, d.event_type, d.idempotency_key,
        )
        n += 1
    return n


def run_daily_pass(
    *,
    customer_contexts: Iterable[RolloverContext],
    dispatcher: Callable[[Iterable[SchedulingDecision]], int] = _inert_dispatcher,
    audit: Optional[LifecycleAudit] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Top-level entry point — what the Celery task body should call.

    Parameters mirror what the Flask wiring layer assembles:
      - customer_contexts: rows already filtered to "live" customers.
      - dispatcher: side-effect target (emails, SF inserts, etc.).
      - audit: LifecycleAudit instance for idempotency dedupe.

    Returns a summary dict for the worker heartbeat / ops monitor.
    """
    all_decisions: list[SchedulingDecision] = []
    for ctx in customer_contexts:
        if audit is not None:
            ctx = _hydrate_audit_keys(ctx, audit, _today_sl(now))
        all_decisions.extend(compute_decisions_for_customer(ctx, now=now))

    dispatched = dispatcher(all_decisions)

    if audit is not None:
        # Stamp audit rows with the scheduler's "now" so test harnesses that
        # simulate a future date (or backfill passes) can correctly query
        # them with `since=date_in_the_future - 7d`. In production now=None
        # so this falls through to datetime.now(timezone.utc).
        when = None
        if now is not None:
            when = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        for d in all_decisions:
            audit.record(
                event_type=d.event_type,
                actor="rollover_scheduler",
                target_id=d.customer_id,
                idempotency_key=d.idempotency_key,
                payload=d.payload,
                created_at=when,
            )

    return {
        "decisions_computed": len(all_decisions),
        "dispatched": dispatched,
        "as_of_sl_date": _today_sl(now).isoformat(),
    }


def _hydrate_audit_keys(
    ctx: RolloverContext, audit: LifecycleAudit, today: date
) -> RolloverContext:
    """Pull recently-fired idempotency keys for this customer into the ctx so
    compute_decisions_for_customer can dedupe."""
    # Window: today plus the prior week (handles backfill after outage).
    since = today - timedelta(days=7)
    keys = tuple(audit.recent_keys(target_id=ctx.customer_id, since=since))
    return RolloverContext(
        customer_id=ctx.customer_id,
        persona=ctx.persona,
        prior_year_filed=ctx.prior_year_filed,
        has_prior_tax_file=ctx.has_prior_tax_file,
        has_current_tax_file=ctx.has_current_tax_file,
        sp_count=ctx.sp_count,
        rental_count=ctx.rental_count,
        last_audit_keys=ctx.last_audit_keys + keys,
    )


__all__ = [
    "FILING_REMINDER_OFFSETS_DAYS",
    "YEAR_BOUNDARY_REMINDER_OFFSETS_DAYS",
    "RolloverContext",
    "SchedulingDecision",
    "compute_decisions_for_customer",
    "run_daily_pass",
]
