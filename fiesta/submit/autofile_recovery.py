"""
fiesta.submit.autofile_recovery — Auto-File failure-mode recovery (v1.0).

Per v1.0 roadmap (post-council R3 v2 plan):
    Detect IRD-side rejection -> exponential-backoff re-queue (3 attempts
    over 1h) -> user-facing S14 "submission pending, retry N/3" state
    -> Resolver_Action opened -> dispatch_alert after 3 failures
    -> manual-confirm CTA exposed to user.

This module is the framework — the IRD-portal automation itself (S14 W5)
is built separately and is blocked on G.1.5 IRD walkthrough screenshots.
The framework is decoupled from W5 so it can ship in v1.0 and W5 can ship
on top of it once unblocked.

Public API (called by the W5 automation_runner port + the Celery sweeper):

  >>> from fiesta.submit.autofile_recovery import (
  ...     record_attempt_failure, record_attempt_success,
  ...     process_pending_retries, MAX_ATTEMPTS, RETRY_BACKOFF_SECONDS,
  ... )
  >>> # When IRD returns a rejection / 5xx / timeout, the W5 caller invokes:
  >>> record_attempt_failure(submission_id=123, error="IRD 500: server busy")
  >>> # When IRD acknowledges the submission:
  >>> record_attempt_success(submission_id=123)
  >>> # Celery beat fires every minute:
  >>> process_pending_retries()      # iterates due retries, dispatches W5

Retry schedule (exponential backoff):
  attempt 1 failure -> next retry at +10 min  (autofile_next_retry_at)
  attempt 2 failure -> next retry at +30 min
  attempt 3 failure -> status='autofile-failed-needs-manual'
                       Resolver_Action row created (best-effort)
                       dispatch_alert via existing ops_sentinel path

Note on alerting: per the existing ops_sentinel.py module contract
"DO NOT page Telegram from FIESTA" (council #2 explicit), failure
escalation goes via the established Event-Spine + SendGrid path
(ops_sentinel.dispatch_alert). The v2 roadmap line item "Telegram CEO
after 3 failures" is satisfied by the existing CEO-OS-side bridge that
watches the ops_alert Event-Spine row stream — the FIESTA side does NOT
call Telegram directly.

User-facing state on S14: the route handler reads submission.status +
autofile_attempt_count and renders the appropriate template branch:

  - 'autofile-pending-retry' + attempt < 3 -> "submission pending,
    retry N/3 in <next_retry delta>" + cancel-and-manual-confirm CTA
  - 'autofile-failed-needs-manual' -> "automatic submission failed —
    please review and confirm manually" + manual-confirm CTA primary
  - 'autofile-succeeded' -> "submitted to IRD on <date>; ack <ack#>"

This module is dependency-injectable for tests:
  - `_default_dispatch_alert`: from ops_sentinel — overridable via
    `set_dispatch_alert_callable()` for tests that don't want to write
    Event rows.
  - `_default_resolver_action_writer`: stub by default (W5 wires the real
    SF write when it ships).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants.
# --------------------------------------------------------------------------- #

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: List[int] = [
    10 * 60,    # attempt 1 failure → 10 min until attempt 2
    30 * 60,    # attempt 2 failure → 30 min until attempt 3
    # No backoff after attempt 3 — escalate to manual-confirm.
]

# Status values FIESTA writes (Submission.status is VARCHAR(32) — no enum).
STATUS_AUTOFILE_PENDING_RETRY = "autofile-pending-retry"
STATUS_AUTOFILE_FAILED_NEEDS_MANUAL = "autofile-failed-needs-manual"
STATUS_AUTOFILE_SUCCEEDED = "autofile-succeeded"


# --------------------------------------------------------------------------- #
# Dependency-injectable hooks (overridable in tests).
# --------------------------------------------------------------------------- #

DispatchAlertCallable = Callable[[str, Dict[str, Any]], Optional[int]]
ResolverActionWriterCallable = Callable[[Dict[str, Any]], Optional[str]]


def _default_dispatch_alert(check_name: str, check_result: Dict[str, Any]) -> Optional[int]:
    """Default alert dispatcher — routes through ops_sentinel.dispatch_alert
    (Event-Spine + SendGrid). Returns Event id."""
    try:
        from ops_sentinel import dispatch_alert
        return dispatch_alert(check_name, check_result)
    except Exception as exc:
        log.warning("autofile_recovery dispatch_alert failed: %s", exc)
        return None


def _default_resolver_action_writer(payload: Dict[str, Any]) -> Optional[str]:
    """Default Resolver_Action writer — STUB until W5 SF integration ships.

    The real implementation (added in v1.x when SF Resolver_Action
    creation is built into FIESTA, OR delegated to CEO-OS via send_gate):
        creates a Resolver_Action__c with classifier_inputs JSON capturing
        the submission_id, customer_id, attempt history, and error trace.

    For now we log + return None so the caller knows the SF row was not
    written, but the rest of the failure path still runs cleanly.
    """
    log.info(
        "autofile_recovery: Resolver_Action stub (would write SF row): %s",
        {k: v for k, v in payload.items() if k != "error_traceback"},
    )
    return None


_dispatch_alert: DispatchAlertCallable = _default_dispatch_alert
_resolver_action_writer: ResolverActionWriterCallable = _default_resolver_action_writer


def set_dispatch_alert_callable(fn: DispatchAlertCallable) -> None:
    """Test hook — override the alert dispatcher."""
    global _dispatch_alert
    _dispatch_alert = fn


def set_resolver_action_writer(fn: ResolverActionWriterCallable) -> None:
    """Test hook — override the Resolver_Action writer."""
    global _resolver_action_writer
    _resolver_action_writer = fn


def reset_hooks_to_default() -> None:
    """Restore both hooks to their module defaults (tests cleanup)."""
    global _dispatch_alert, _resolver_action_writer
    _dispatch_alert = _default_dispatch_alert
    _resolver_action_writer = _default_resolver_action_writer


# --------------------------------------------------------------------------- #
# Public API.
# --------------------------------------------------------------------------- #

def record_attempt_failure(*, submission_id: int, error: str,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Increment attempt_count; on attempts 1+2 schedule the next retry;
    on attempt 3 transition to failed-needs-manual + escalate.

    Returns a dict with the new state for caller logging:
      {
        "submission_id": int,
        "attempt_count": int,
        "status": str,
        "next_retry_at": iso-string | None,
        "escalated": bool,
      }
    """
    from app import db
    from .models import Submission
    now = now or datetime.utcnow()

    sub = Submission.query.get(submission_id)
    if sub is None:
        return {"submission_id": submission_id, "attempt_count": 0,
                "status": None, "next_retry_at": None,
                "escalated": False, "error": "submission_not_found"}

    new_attempt = (sub.autofile_attempt_count or 0) + 1
    sub.autofile_attempt_count = new_attempt
    sub.autofile_last_attempted_at = now
    sub.autofile_last_error = (error or "")[:500]

    escalated = False
    if new_attempt >= MAX_ATTEMPTS:
        sub.status = STATUS_AUTOFILE_FAILED_NEEDS_MANUAL
        sub.autofile_next_retry_at = None
        escalated = True
    else:
        # Compute next retry timestamp from the backoff schedule.
        # Index is attempt_count - 1 (after-1st-failure uses index 0).
        backoff_idx = min(new_attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
        sub.autofile_next_retry_at = now + timedelta(
            seconds=RETRY_BACKOFF_SECONDS[backoff_idx]
        )
        sub.status = STATUS_AUTOFILE_PENDING_RETRY

    db.session.commit()

    if escalated:
        _escalate_to_manual(sub, error=error)

    return {
        "submission_id": submission_id,
        "attempt_count": new_attempt,
        "status": sub.status,
        "next_retry_at": (sub.autofile_next_retry_at.isoformat() + "Z"
                           if sub.autofile_next_retry_at else None),
        "escalated": escalated,
    }


def record_attempt_success(*, submission_id: int,
                            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Mark a submission as successfully filed.

    The W5 caller hands us the success signal AFTER IRD acknowledges. The
    caller is also responsible for writing IRD ack number / receipt rows
    via the existing IrdConfirmationReceipt model — this function only
    flips the Submission status + clears retry state.
    """
    from app import db
    from .models import Submission
    now = now or datetime.utcnow()

    sub = Submission.query.get(submission_id)
    if sub is None:
        return {"submission_id": submission_id, "status": None,
                "error": "submission_not_found"}

    sub.status = STATUS_AUTOFILE_SUCCEEDED
    sub.autofile_last_attempted_at = now
    sub.autofile_next_retry_at = None
    sub.autofile_last_error = None
    db.session.commit()

    return {
        "submission_id": submission_id,
        "status": sub.status,
        "attempt_count": sub.autofile_attempt_count or 0,
    }


def process_pending_retries(*,
                              now: Optional[datetime] = None,
                              dispatch_fn: Optional[Callable[[int], Dict[str, Any]]] = None,
                              limit: int = 50) -> Dict[str, Any]:
    """Find submissions due for a retry (autofile_next_retry_at <= now AND
    status == autofile-pending-retry) and dispatch the W5 automation_runner
    for each. Idempotent: a retry that's already in flight (handled by
    automation_runner's own dedup) is no-op.

    Called by the Celery beat task every minute. Returns a summary dict
    for logging:
      {"checked_at": iso, "due_count": int, "dispatched": int,
       "skipped": int}

    The ``dispatch_fn`` parameter is the W5 automation_runner callable.
    Default is a no-op so this module can ship in v1.0 BEFORE W5 lands.
    """
    from .models import Submission
    now = now or datetime.utcnow()

    due = (
        Submission.query
        .filter(Submission.status == STATUS_AUTOFILE_PENDING_RETRY)
        .filter(Submission.autofile_next_retry_at != None)  # noqa: E711
        .filter(Submission.autofile_next_retry_at <= now)
        .order_by(Submission.autofile_next_retry_at.asc())
        .limit(limit)
        .all()
    )

    dispatched = 0
    skipped = 0
    for sub in due:
        if dispatch_fn is None:
            # No W5 wired yet (v1.0 framework-only ship). Skip cleanly +
            # log; W5 will wire its dispatcher in via this same API once
            # the IRD walkthrough screenshots land.
            skipped += 1
            log.debug(
                "autofile_recovery: submission %s due but W5 dispatcher not "
                "wired (framework-only mode); skipping retry tick",
                sub.id,
            )
            continue
        try:
            dispatch_fn(sub.id)
            dispatched += 1
        except Exception as exc:
            log.warning(
                "autofile_recovery: dispatch_fn raised for submission %s: %s",
                sub.id, exc,
            )
            # The failure is recorded via record_attempt_failure by the
            # automation_runner caller, NOT here — we don't want to
            # double-record.
            skipped += 1

    return {
        "checked_at": now.isoformat() + "Z",
        "due_count": len(due),
        "dispatched": dispatched,
        "skipped": skipped,
    }


def get_user_visible_state(*, submission_id: int,
                             now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return the user-visible state for S14 rendering.

    Shape (consumed by the S14 template branch):
      {
        "status": "autofile-pending-retry" | "autofile-failed-needs-manual" |
                  "autofile-succeeded" | "<other-non-autofile-status>",
        "attempt_count": int,
        "max_attempts": int,
        "next_retry_in_seconds": int | None,
        "last_error": str | None,
        "needs_manual_confirm": bool,
      }

    Pure read — no DB writes. Safe to call from the GET route handler.
    """
    from .models import Submission
    now = now or datetime.utcnow()

    sub = Submission.query.get(submission_id)
    if sub is None:
        return {"status": None, "attempt_count": 0,
                "max_attempts": MAX_ATTEMPTS,
                "next_retry_in_seconds": None, "last_error": None,
                "needs_manual_confirm": False}

    delta_s: Optional[int] = None
    if sub.autofile_next_retry_at is not None:
        delta = (sub.autofile_next_retry_at - now).total_seconds()
        delta_s = max(0, int(delta))

    return {
        "status": sub.status,
        "attempt_count": int(sub.autofile_attempt_count or 0),
        "max_attempts": MAX_ATTEMPTS,
        "next_retry_in_seconds": delta_s,
        "last_error": sub.autofile_last_error,
        "needs_manual_confirm": (
            sub.status == STATUS_AUTOFILE_FAILED_NEEDS_MANUAL
        ),
    }


# --------------------------------------------------------------------------- #
# Escalation helpers.
# --------------------------------------------------------------------------- #

def _escalate_to_manual(sub, *, error: str) -> None:
    """Open a Resolver_Action (best-effort) + dispatch an alert via the
    established ops_sentinel path.

    Called once per submission at the 3rd-attempt failure transition.
    """
    payload = {
        "submission_id": sub.id,
        "user_id": sub.user_id,
        "tax_year": sub.tax_year,
        "attempt_count": sub.autofile_attempt_count,
        "last_error": error[:500],
        "last_attempted_at": (sub.autofile_last_attempted_at.isoformat() + "Z"
                                if sub.autofile_last_attempted_at else None),
        "needs_manual_confirm": True,
        "f_code": "F-AUTOFILE-FAILED",
    }
    try:
        ra_id = _resolver_action_writer(payload)
        payload["resolver_action_id"] = ra_id
    except Exception as exc:
        log.warning(
            "autofile_recovery: resolver_action_writer failed for sub %s: %s",
            sub.id, exc,
        )
        payload["resolver_action_id"] = None

    check_result = {
        "healthy": False,
        "value": f"submission {sub.id} attempt {sub.autofile_attempt_count} failed",
        "threshold": f"< {MAX_ATTEMPTS} attempts",
        "message": (
            f"Auto-File submission {sub.id} (user {sub.user_id}, "
            f"tax year {sub.tax_year}) exhausted {MAX_ATTEMPTS} retry "
            f"attempts. Last error: {error[:200]}. Customer S14 now shows "
            f"manual-confirm CTA. Resolver_Action id: {payload['resolver_action_id']}"
        ),
    }
    try:
        _dispatch_alert("autofile_submission_exhausted", check_result)
    except Exception as exc:
        log.warning(
            "autofile_recovery: dispatch_alert failed for sub %s: %s",
            sub.id, exc,
        )


__all__ = [
    "MAX_ATTEMPTS",
    "RETRY_BACKOFF_SECONDS",
    "STATUS_AUTOFILE_PENDING_RETRY",
    "STATUS_AUTOFILE_FAILED_NEEDS_MANUAL",
    "STATUS_AUTOFILE_SUCCEEDED",
    "record_attempt_failure",
    "record_attempt_success",
    "process_pending_retries",
    "get_user_visible_state",
    "set_dispatch_alert_callable",
    "set_resolver_action_writer",
    "reset_hooks_to_default",
]
