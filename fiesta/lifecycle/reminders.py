"""fiesta.lifecycle.reminders — outbound dispatch for X3 + S11 events.

Why a thin dispatch layer here rather than directly in the scheduler:
  - The scheduler emits SchedulingDecision / ReminderTrigger objects
    (pure data). This module turns them into actual side-effects:
    email, in-app notification, audit log entry.
  - Tests can swap the dispatcher for a recorder. Production wires the
    repo-root email service.
  - Keeps the FIESTA email service decoupled from lifecycle so an email-
    template rename doesn't ripple into rollover_scheduler.

Side-effects this module performs:
  - Calls the injected email_sender callable with (customer_id, subject,
    body_text, body_html).
  - Calls the injected in_app_sender callable for in-app banners.
  - Writes to LifecycleAudit (idempotency + 7-year IRD retention).

Side-effects this module does NOT perform:
  - No SQL writes (caller passes hydrated customer dicts).
  - No SF API calls — those go via fiesta.delivery_ops.automation_runner.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable, Optional, Protocol

from .audit_log import LifecycleAudit
from .invoice_cadence import ReminderTrigger
from .rollover_scheduler import SchedulingDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sender protocols — caller injects implementations.
# ---------------------------------------------------------------------------


class EmailSender(Protocol):
    def __call__(
        self,
        *,
        customer_id: int,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> str: ...  # returns provider message id


class InAppSender(Protocol):
    def __call__(
        self, *, customer_id: int, banner_kind: str, payload: dict
    ) -> None: ...


# ---------------------------------------------------------------------------
# Template body builders — single source for reminder copy.
# Edit here when copy changes; everything else is plumbing.
# ---------------------------------------------------------------------------


def _build_x3_body(event_type: str, payload: dict) -> tuple[str, str]:
    """Year-end transition copy. Returns (subject, body_text)."""
    py = payload.get("prior_year", "prior year")
    ny = payload.get("current_year", "new year")

    if event_type == "year_closing_tomorrow":
        return (
            f"Tax year {py} ends tomorrow",
            (
                f"Hi,\n\n"
                f"Sri Lanka's tax year {py} ends tomorrow (31 March).\n\n"
                f"Take 5 minutes to:\n"
                f"  - Review any pending invoices for {py}.\n"
                f"  - Confirm rental agreements that span both years.\n"
                f"  - Note any service-provider contracts ending this year.\n\n"
                f"On 1 April we'll set you up for tax year {ny} with your "
                f"existing service providers carried over.\n\n"
                f"— FIESTA"
            ),
        )

    if event_type == "year_ended_today":
        # Internal log only — no customer message. Use silent placeholder.
        return ("(internal) year-end logged", "")

    if event_type == "new_year_transition_invite":
        return (
            f"Welcome to tax year {ny}",
            (
                f"Hi,\n\n"
                f"Tax year {ny} starts today. We've prepared your transition "
                f"checklist:\n\n"
                f"  - Service providers: auto-carried over (review the list "
                f"    and remove any you no longer use).\n"
                f"  - Rental agreements: confirm renewals before adding "
                f"    {ny} rent invoices.\n"
                f"  - Bank details: unchanged — let us know if anything moved.\n"
                f"  - Persona: still set from last year.\n\n"
                f"Your {py} return: due 30 November {_filing_year(py)}. "
                f"We'll remind you 30 days, 7 days, and 1 day before.\n\n"
                f"Start {ny}: visit your dashboard.\n\n"
                f"— FIESTA"
            ),
        )

    if event_type == "filing_deadline_approaching":
        days = abs(int(payload.get("days_offset", 0)))
        yr = payload.get("year", py)
        return (
            f"Tax year {yr} filing deadline in {days} day(s)",
            (
                f"Hi,\n\n"
                f"Your {yr} return is due in {days} day(s). "
                f"Submit before 30 November to avoid penalties.\n\n"
                f"Open dashboard to finish your return.\n\n"
                f"— FIESTA"
            ),
        )

    if event_type == "filing_deadline_overdue":
        days = int(payload.get("days_offset", 0))
        yr = payload.get("year", py)
        return (
            f"Tax year {yr} filing overdue by {days} day(s)",
            (
                f"Hi,\n\n"
                f"Your {yr} return was due {days} day(s) ago. "
                f"Filing late incurs penalties under IRA s.178. "
                f"Submit now to limit further penalties.\n\n"
                f"— FIESTA"
            ),
        )

    if event_type == "create_new_year_tax_file":
        return ("(internal) new-year tax file created", "")

    return ("(unknown event)", "")


def _build_s11_body(trig: ReminderTrigger) -> tuple[str, str]:
    """Invoice cadence copy."""
    if trig.reminder_kind == "monthly_invoice_due_soon":
        return (
            "Upcoming invoice — add it when you receive it",
            f"Hi,\n\n{trig.message_hint}\n\nFIESTA will prompt again on the "
            f"due date if it isn't added.\n\n— FIESTA",
        )
    if trig.reminder_kind == "monthly_invoice_missing":
        return (
            "Missing invoice for a recurring service provider",
            f"Hi,\n\n{trig.message_hint} Add it if you have it, or note that "
            f"this period was skipped.\n\n— FIESTA",
        )
    if trig.reminder_kind == "quarterly_cycle_starts":
        return (
            "Quarterly invoice cycle starts soon",
            f"Hi,\n\n{trig.message_hint}\n\n— FIESTA",
        )
    if trig.reminder_kind == "next_due_after_irregular_gap":
        return (
            "Review cadence for a service provider",
            f"Hi,\n\n{trig.message_hint}\n\n— FIESTA",
        )
    return ("(unknown cadence event)", "")


def _filing_year(yoa_label: str) -> str:
    """For YoA '24/25' (ends 31 Mar 2025) -> filing year 2025."""
    parts = yoa_label.split("/")
    return f"20{parts[1]}" if len(parts) == 2 else yoa_label


# ---------------------------------------------------------------------------
# Dispatchers
# ---------------------------------------------------------------------------


@dataclass
class ReminderDispatchResult:
    sent: int = 0
    suppressed_duplicates: int = 0
    skipped_silent: int = 0
    errors: int = 0


def dispatch_x3(
    decisions: Iterable[SchedulingDecision],
    *,
    email_sender: Optional[EmailSender] = None,
    in_app_sender: Optional[InAppSender] = None,
    audit: Optional[LifecycleAudit] = None,
) -> ReminderDispatchResult:
    """Send year-end transition messages.

    Idempotency: each decision carries an idempotency_key. If audit
    already has that key, suppress as duplicate.
    """
    result = ReminderDispatchResult()

    for d in decisions:
        if audit is not None and audit.was_recorded(d.idempotency_key):
            result.suppressed_duplicates += 1
            continue

        subject, body = _build_x3_body(d.event_type, d.payload)
        if not body:
            # Silent / internal-only events still get audited.
            result.skipped_silent += 1
            _log_dispatch(audit, d.customer_id, d.event_type,
                          d.idempotency_key, d.payload, sent=False)
            continue

        try:
            if email_sender is not None:
                email_sender(
                    customer_id=d.customer_id,
                    subject=subject,
                    body_text=body,
                    meta={"event_type": d.event_type,
                          "idempotency_key": d.idempotency_key},
                )
            if in_app_sender is not None:
                in_app_sender(
                    customer_id=d.customer_id,
                    banner_kind=d.event_type,
                    payload=d.payload,
                )
            result.sent += 1
            _log_dispatch(audit, d.customer_id, d.event_type,
                          d.idempotency_key, d.payload, sent=True)
        except Exception:
            logger.exception(
                "reminder dispatch failed customer=%s event=%s",
                d.customer_id, d.event_type,
            )
            result.errors += 1

    return result


def dispatch_s11(
    triggers: Iterable[ReminderTrigger],
    *,
    email_sender: Optional[EmailSender] = None,
    in_app_sender: Optional[InAppSender] = None,
    audit: Optional[LifecycleAudit] = None,
) -> ReminderDispatchResult:
    """Send invoice cadence reminders."""
    result = ReminderDispatchResult()

    for t in triggers:
        if audit is not None and audit.was_recorded(t.idempotency_key):
            result.suppressed_duplicates += 1
            continue

        subject, body = _build_s11_body(t)
        try:
            if email_sender is not None:
                email_sender(
                    customer_id=t.customer_id,
                    subject=subject,
                    body_text=body,
                    meta={"reminder_kind": t.reminder_kind,
                          "sp_id": t.sp_id,
                          "idempotency_key": t.idempotency_key},
                )
            if in_app_sender is not None:
                in_app_sender(
                    customer_id=t.customer_id,
                    banner_kind=f"s11_{t.reminder_kind}",
                    payload={"sp_id": t.sp_id, "due_date": t.due_date.isoformat()},
                )
            result.sent += 1
            _log_dispatch(
                audit, t.customer_id, f"s11_{t.reminder_kind}",
                t.idempotency_key,
                {"sp_id": t.sp_id, "due_date": t.due_date.isoformat()},
                sent=True,
            )
        except Exception:
            logger.exception(
                "S11 reminder dispatch failed customer=%s sp=%s kind=%s",
                t.customer_id, t.sp_id, t.reminder_kind,
            )
            result.errors += 1

    return result


def _log_dispatch(
    audit: Optional[LifecycleAudit],
    customer_id: int,
    event_type: str,
    idempotency_key: str,
    payload: dict,
    *,
    sent: bool,
) -> None:
    if audit is None:
        return
    audit.record(
        event_type=f"reminder.{event_type}",
        actor="reminders.dispatcher",
        target_id=customer_id,
        idempotency_key=idempotency_key,
        payload={**payload, "sent": sent},
    )


__all__ = [
    "EmailSender",
    "InAppSender",
    "ReminderDispatchResult",
    "dispatch_x3",
    "dispatch_s11",
]
