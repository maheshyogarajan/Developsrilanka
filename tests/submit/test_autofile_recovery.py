"""
fiesta.submit.autofile_recovery tests (v1.0 — Wave 6, 2026-05-21).

Validates the failure-mode recovery framework that ships in v1.0 BEFORE
the W5 IRD automation_runner port (which is blocked on G.1.5 IRD
walkthrough screenshots). The framework is exercised end-to-end here so
that when W5 wires its dispatch_fn, the retry-machine is already proven.

Coverage:
  - record_attempt_failure (1st, 2nd, 3rd attempts) transitions correctly
  - exponential backoff times line up with RETRY_BACKOFF_SECONDS
  - 3rd attempt -> 'autofile-failed-needs-manual' + escalation hooks fire
  - record_attempt_success clears retry state
  - process_pending_retries finds due rows + calls dispatch_fn
  - process_pending_retries with no dispatch_fn (framework-only mode)
    skips cleanly
  - get_user_visible_state returns the S14-renderable shape
  - escalation calls inject-able hooks (no live SF / SendGrid writes)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from tests.remittance.conftest import login_as  # noqa: F401 (reuse fixtures)


# --------------------------------------------------------------------------- #
# Helper: create a Submission row directly so tests don't have to walk the
# full S14 flow.
# --------------------------------------------------------------------------- #

@pytest.fixture
def submission_factory(app, db_session, user_a):
    """Yields (callable) -> Submission row. Cleans up on teardown."""
    created_ids = []

    def _make(*, status="attested", tax_year="2025/2026", **overrides):
        from fiesta.submit.models import Submission
        from app import db

        defaults = {
            "user_id": user_a.id,
            "tax_year": tax_year,
            "status": status,
            "customer_acknowledged_warnings_json": "[]",
            "gate_snapshot_json": "{}",
            "autofile_attempt_count": 0,
        }
        defaults.update(overrides)
        s = Submission(**defaults)
        db.session.add(s)
        db.session.commit()
        created_ids.append(s.id)
        return s

    yield _make

    from fiesta.submit.models import Submission, SubmissionAuditEvent
    from app import db
    if created_ids:
        SubmissionAuditEvent.query.filter(
            SubmissionAuditEvent.submission_id.in_(created_ids)
        ).delete(synchronize_session=False)
        Submission.query.filter(
            Submission.id.in_(created_ids)
        ).delete(synchronize_session=False)
        db.session.commit()


@pytest.fixture(autouse=True)
def _reset_hooks_after_each_test():
    """Belt-and-braces — every test starts with the module hooks at their
    defaults. Tests that override should restore via the autouse teardown."""
    yield
    try:
        from fiesta.submit.autofile_recovery import reset_hooks_to_default
        reset_hooks_to_default()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# record_attempt_failure
# --------------------------------------------------------------------------- #

def test_first_failure_schedules_10min_retry(app, submission_factory):
    from fiesta.submit.autofile_recovery import (
        record_attempt_failure, RETRY_BACKOFF_SECONDS,
        STATUS_AUTOFILE_PENDING_RETRY,
    )
    sub = submission_factory()
    now = datetime(2026, 5, 21, 12, 0, 0)

    with app.app_context():
        result = record_attempt_failure(
            submission_id=sub.id,
            error="IRD 500: server busy",
            now=now,
        )
    assert result["attempt_count"] == 1
    assert result["status"] == STATUS_AUTOFILE_PENDING_RETRY
    assert result["escalated"] is False
    # +600s = 10 min per RETRY_BACKOFF_SECONDS[0]
    expected = (now + timedelta(seconds=RETRY_BACKOFF_SECONDS[0])).isoformat() + "Z"
    assert result["next_retry_at"] == expected


def test_second_failure_schedules_30min_retry(app, submission_factory):
    from fiesta.submit.autofile_recovery import (
        record_attempt_failure, RETRY_BACKOFF_SECONDS,
        STATUS_AUTOFILE_PENDING_RETRY,
    )
    sub = submission_factory(autofile_attempt_count=1)
    now = datetime(2026, 5, 21, 12, 30, 0)

    with app.app_context():
        result = record_attempt_failure(
            submission_id=sub.id,
            error="IRD timeout",
            now=now,
        )
    assert result["attempt_count"] == 2
    assert result["status"] == STATUS_AUTOFILE_PENDING_RETRY
    assert result["escalated"] is False
    expected = (now + timedelta(seconds=RETRY_BACKOFF_SECONDS[1])).isoformat() + "Z"
    assert result["next_retry_at"] == expected


def test_third_failure_escalates_to_manual(app, submission_factory):
    """The 3rd failure must (a) flip status to failed-needs-manual,
    (b) clear next_retry_at, (c) call the alert dispatcher,
    (d) call the resolver_action writer."""
    from fiesta.submit.autofile_recovery import (
        record_attempt_failure,
        STATUS_AUTOFILE_FAILED_NEEDS_MANUAL,
        set_dispatch_alert_callable,
        set_resolver_action_writer,
    )
    sub = submission_factory(autofile_attempt_count=2)
    now = datetime(2026, 5, 21, 13, 0, 0)

    alerts_fired = []
    resolver_writes = []
    set_dispatch_alert_callable(
        lambda name, result: (alerts_fired.append((name, result)) or 99)
    )
    set_resolver_action_writer(
        lambda payload: (resolver_writes.append(payload) or "aXX_RA_test_001")
    )

    with app.app_context():
        result = record_attempt_failure(
            submission_id=sub.id,
            error="IRD rejected: missing PIN_Valid",
            now=now,
        )
    assert result["attempt_count"] == 3
    assert result["status"] == STATUS_AUTOFILE_FAILED_NEEDS_MANUAL
    assert result["next_retry_at"] is None
    assert result["escalated"] is True
    # Escalation side-effects:
    assert len(alerts_fired) == 1, alerts_fired
    assert alerts_fired[0][0] == "autofile_submission_exhausted"
    assert "manual-confirm" in alerts_fired[0][1]["message"]
    assert len(resolver_writes) == 1
    payload = resolver_writes[0]
    assert payload["submission_id"] == sub.id
    assert payload["f_code"] == "F-AUTOFILE-FAILED"
    assert payload["needs_manual_confirm"] is True


def test_record_attempt_success_clears_retry_state(app, submission_factory):
    from fiesta.submit.autofile_recovery import (
        record_attempt_success, STATUS_AUTOFILE_SUCCEEDED,
    )
    sub = submission_factory(
        status="autofile-pending-retry",
        autofile_attempt_count=2,
        autofile_next_retry_at=datetime(2026, 5, 21, 14, 0, 0),
        autofile_last_error="prior transient error",
    )
    with app.app_context():
        result = record_attempt_success(submission_id=sub.id)
    assert result["status"] == STATUS_AUTOFILE_SUCCEEDED

    # Re-read.
    from fiesta.submit.models import Submission
    with app.app_context():
        s2 = Submission.query.get(sub.id)
        assert s2.status == STATUS_AUTOFILE_SUCCEEDED
        assert s2.autofile_next_retry_at is None
        assert s2.autofile_last_error is None


# --------------------------------------------------------------------------- #
# process_pending_retries
# --------------------------------------------------------------------------- #

def test_process_pending_retries_skips_when_no_dispatch_fn(app, submission_factory):
    """Framework-only mode (no W5 dispatcher wired): due rows are found
    but the sweeper skips cleanly without raising."""
    from fiesta.submit.autofile_recovery import (
        process_pending_retries, STATUS_AUTOFILE_PENDING_RETRY,
    )
    past = datetime(2026, 5, 21, 11, 0, 0)
    sub = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=1,
        autofile_next_retry_at=past,  # past = due
    )

    now = datetime(2026, 5, 21, 12, 0, 0)
    with app.app_context():
        summary = process_pending_retries(now=now)
    assert summary["due_count"] >= 1
    assert summary["dispatched"] == 0
    assert summary["skipped"] >= 1


def test_process_pending_retries_calls_dispatch_fn(app, submission_factory):
    """When a W5 dispatcher is provided, it is called once per due row."""
    from fiesta.submit.autofile_recovery import (
        process_pending_retries, STATUS_AUTOFILE_PENDING_RETRY,
    )
    past = datetime(2026, 5, 21, 11, 0, 0)
    sub_a = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=1,
        autofile_next_retry_at=past,
    )
    sub_b = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=2,
        autofile_next_retry_at=past - timedelta(minutes=5),  # older = first
    )

    dispatched = []
    def _stub_dispatch(submission_id: int):
        dispatched.append(submission_id)

    now = datetime(2026, 5, 21, 12, 0, 0)
    with app.app_context():
        summary = process_pending_retries(now=now, dispatch_fn=_stub_dispatch)

    assert sub_a.id in dispatched
    assert sub_b.id in dispatched
    # Order should be oldest-first (sub_b before sub_a):
    assert dispatched.index(sub_b.id) < dispatched.index(sub_a.id)
    assert summary["dispatched"] == len(dispatched)


def test_process_pending_retries_skips_future_retries(app, submission_factory):
    """Rows whose next_retry_at is FUTURE are NOT picked up by the sweeper."""
    from fiesta.submit.autofile_recovery import (
        process_pending_retries, STATUS_AUTOFILE_PENDING_RETRY,
    )
    future = datetime(2026, 5, 21, 13, 0, 0)
    sub = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=1,
        autofile_next_retry_at=future,
    )
    dispatched = []
    now = datetime(2026, 5, 21, 12, 0, 0)
    with app.app_context():
        summary = process_pending_retries(
            now=now, dispatch_fn=lambda sid: dispatched.append(sid),
        )
    assert sub.id not in dispatched


def test_process_pending_retries_dispatch_fn_exception_does_not_crash_sweeper(
        app, submission_factory):
    """A buggy dispatch_fn raises; the sweeper logs + continues to the next row."""
    from fiesta.submit.autofile_recovery import (
        process_pending_retries, STATUS_AUTOFILE_PENDING_RETRY,
    )
    past = datetime(2026, 5, 21, 11, 0, 0)
    sub_a = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=1,
        autofile_next_retry_at=past,
    )
    sub_b = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=1,
        autofile_next_retry_at=past,
    )

    calls = []
    def _crashy(sid):
        calls.append(sid)
        if sid == sub_a.id:
            raise RuntimeError("simulated W5 crash")

    now = datetime(2026, 5, 21, 12, 0, 0)
    with app.app_context():
        summary = process_pending_retries(now=now, dispatch_fn=_crashy)

    # Both rows were attempted.
    assert sub_a.id in calls
    assert sub_b.id in calls
    # One succeeded (sub_b), one was skipped due to exception (sub_a).
    assert summary["dispatched"] == 1
    assert summary["skipped"] == 1


# --------------------------------------------------------------------------- #
# get_user_visible_state
# --------------------------------------------------------------------------- #

def test_get_user_visible_state_pending_retry(app, submission_factory):
    from fiesta.submit.autofile_recovery import (
        get_user_visible_state, STATUS_AUTOFILE_PENDING_RETRY,
    )
    next_retry = datetime(2026, 5, 21, 12, 10, 0)
    sub = submission_factory(
        status=STATUS_AUTOFILE_PENDING_RETRY,
        autofile_attempt_count=2,
        autofile_next_retry_at=next_retry,
        autofile_last_error="IRD timeout (transient)",
    )
    now = datetime(2026, 5, 21, 12, 0, 0)
    with app.app_context():
        state = get_user_visible_state(submission_id=sub.id, now=now)
    assert state["status"] == STATUS_AUTOFILE_PENDING_RETRY
    assert state["attempt_count"] == 2
    assert state["max_attempts"] == 3
    assert state["next_retry_in_seconds"] == 600  # exactly 10 min ahead
    assert state["last_error"] == "IRD timeout (transient)"
    assert state["needs_manual_confirm"] is False


def test_get_user_visible_state_failed_needs_manual(app, submission_factory):
    from fiesta.submit.autofile_recovery import (
        get_user_visible_state, STATUS_AUTOFILE_FAILED_NEEDS_MANUAL,
    )
    sub = submission_factory(
        status=STATUS_AUTOFILE_FAILED_NEEDS_MANUAL,
        autofile_attempt_count=3,
        autofile_next_retry_at=None,
        autofile_last_error="IRD rejected: malformed XML",
    )
    with app.app_context():
        state = get_user_visible_state(submission_id=sub.id)
    assert state["needs_manual_confirm"] is True
    assert state["next_retry_in_seconds"] is None
