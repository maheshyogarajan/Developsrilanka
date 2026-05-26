"""
Markov-L2 state-writer tests.

Coverage matrix (per task brief #11, 2026-05-27):
  1. event_to_state maps each STANDARD_EVENT correctly (transition events
     return an S-state; non-transition events return None).
  2. record_state_transition inserts a row AND sets previous_state_code
     from the prior row.
  3. record_state_transition is a NO-OP if new_state == previous_state
     (no consecutive duplicates).
  4. Backfill produces one row per user (idempotent on re-run).
  5. Defer pattern: emit() doesn't block and doesn't raise even when the
     state writer fails (catch + log).

The fixtures speak to the live Neon DB the same way every other suite
in this repo does (tests/remittance/conftest.py + tests/fiesta_admin/
conftest.py). Helpers below purge UserStateHistory rows for the test
user in teardown.
"""
from __future__ import annotations

import os

import pytest

from tests.fiesta_admin.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    admin_user,
    non_admin_user,
    login_as,
)


def _purge_history(db_session, user_id):
    """Best-effort cleanup of any UserStateHistory rows for this user."""
    try:
        from fiesta.markov.models import UserStateHistory
        UserStateHistory.query.filter(UserStateHistory.user_id == user_id).delete()
        db_session.commit()
    except Exception:
        try:
            db_session.rollback()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 1. event_to_state mapping
# --------------------------------------------------------------------------- #

def test_event_to_state_transition_events_map_to_states():
    """Each transition-bearing STANDARD_EVENT maps to its expected
    S-state. The income-evidence events all land at S04 (Layer 1 owns
    the S05/S06/S07/S08 promotion)."""
    from fiesta.markov.state_writer import event_to_state

    expected = {
        "signup": "S00",
        "checkout_completed": "S01",
        "profile_complete": "S02",
        "remittance_added": "S04",
        "bank_statement_uploaded": "S04",
        "al_completed": "S09",
        "tax_bill_computed": "S10",
        "tax_bill_finalized": "S12",
    }
    for event_name, expected_state in expected.items():
        assert event_to_state(event_name, None, 1) == expected_state, (
            f"event_to_state({event_name!r}) should be {expected_state!r}"
        )


def test_event_to_state_non_transition_events_return_none():
    """Events that DON'T represent a state transition must return None
    so they don't pollute user_state_history."""
    from fiesta.markov.state_writer import event_to_state

    for non_transition in (
        "email_verified",
        "persona_set",
        "remittance_ird_ready",
        "checkout_started",
        "payment_failed",
        "support_message_received",
        "nudge_sent",
        "idea_submitted",
        "profile_validation_error",  # ad-hoc test
        "",                          # empty
        "completely_unknown_event",
    ):
        assert event_to_state(non_transition, None, 1) is None, (
            f"event_to_state({non_transition!r}) should be None"
        )


def test_event_to_state_submission_sentinels_map_to_late_states():
    """Submission-status sentinel triggers map S11/S12/S13/S14 so the
    in-route /submit/* writers can emit transitions for status changes
    that aren't STANDARD_EVENTS."""
    from fiesta.markov.state_writer import event_to_state

    assert event_to_state("submission.awaiting-attestation", None, 1) == "S11"
    assert event_to_state("submission.attested", None, 1) == "S12"
    assert event_to_state("submission.export-generated", None, 1) == "S13"
    assert event_to_state("submission.customer-filed-on-ird", None, 1) == "S14"


# --------------------------------------------------------------------------- #
# 2. record_state_transition inserts a row + sets previous_state_code
# --------------------------------------------------------------------------- #

def test_record_state_transition_inserts_row(app, db_session, admin_user):
    """First write for a user: row exists with previous_state_code=NULL."""
    from fiesta.markov.models import UserStateHistory
    from fiesta.markov.state_writer import record_state_transition

    _purge_history(db_session, admin_user.id)

    with app.app_context():
        row_id = record_state_transition(
            user_id=admin_user.id,
            new_state="S01",
            trigger="checkout_completed",
            metadata={"tier": "self_file"},
        )

    assert row_id is not None
    row = UserStateHistory.query.get(row_id)
    assert row is not None
    assert row.user_id == admin_user.id
    assert row.state_code == "S01"
    assert row.state_label == "Paid / profile pending"
    assert row.previous_state_code is None
    assert row.trigger_event == "checkout_completed"
    assert row.metadata_json == {"tier": "self_file"} or row.metadata_json == '{"tier": "self_file"}'

    _purge_history(db_session, admin_user.id)


def test_record_state_transition_sets_previous_state_from_prior_row(
    app, db_session, admin_user
):
    """Second write inherits previous_state_code from the prior row."""
    from fiesta.markov.models import UserStateHistory
    from fiesta.markov.state_writer import record_state_transition

    _purge_history(db_session, admin_user.id)

    with app.app_context():
        first = record_state_transition(
            user_id=admin_user.id,
            new_state="S01",
            trigger="checkout_completed",
        )
        second = record_state_transition(
            user_id=admin_user.id,
            new_state="S02",
            trigger="profile_complete",
        )

    assert first is not None and second is not None
    first_row = UserStateHistory.query.get(first)
    second_row = UserStateHistory.query.get(second)

    assert first_row.previous_state_code is None
    assert first_row.state_code == "S01"
    assert second_row.previous_state_code == "S01"
    assert second_row.state_code == "S02"

    _purge_history(db_session, admin_user.id)


# --------------------------------------------------------------------------- #
# 3. Consecutive same-state writes are a NO-OP
# --------------------------------------------------------------------------- #

def test_record_state_transition_dedups_consecutive_same_state(
    app, db_session, admin_user
):
    """Writing S03 immediately after S03 must NOT create a second row."""
    from fiesta.markov.models import UserStateHistory
    from fiesta.markov.state_writer import record_state_transition

    _purge_history(db_session, admin_user.id)

    with app.app_context():
        first = record_state_transition(
            user_id=admin_user.id,
            new_state="S03",
            trigger="profile_complete",
        )
        dup = record_state_transition(
            user_id=admin_user.id,
            new_state="S03",
            trigger="profile_complete",
        )

    assert first is not None
    assert dup is None, "consecutive same-state write must be a no-op"

    rows = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .all()
    )
    assert len(rows) == 1, f"expected exactly 1 row, got {len(rows)}"

    _purge_history(db_session, admin_user.id)


def test_record_state_transition_no_op_with_falsy_inputs(app):
    """Writer should silently no-op on missing user_id or state."""
    from fiesta.markov.state_writer import record_state_transition

    with app.app_context():
        assert record_state_transition(user_id=None, new_state="S00", trigger="x") is None
        assert record_state_transition(user_id=0, new_state="S00", trigger="x") is None
        assert record_state_transition(user_id=1, new_state="", trigger="x") is None
        assert record_state_transition(user_id=1, new_state=None, trigger="x") is None


# --------------------------------------------------------------------------- #
# 4. Backfill produces one row per user (idempotent)
# --------------------------------------------------------------------------- #

def test_backfill_produces_one_row_per_user_and_is_idempotent(
    app, db_session, admin_user
):
    """Backfill creates a row for a user without history; re-running
    skips users that already have a row (strict idempotency)."""
    from fiesta.markov.backfill import backfill_all_users
    from fiesta.markov.models import UserStateHistory

    _purge_history(db_session, admin_user.id)

    with app.app_context():
        first = backfill_all_users(commit=True)

    # The admin_user (created by the fixture) should now have exactly one
    # backfill row.
    rows = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .all()
    )
    assert len(rows) == 1, (
        f"expected exactly 1 backfill row for user {admin_user.id}, "
        f"got {len(rows)}"
    )
    row = rows[0]
    assert row.trigger_event == "backfill"
    assert row.previous_state_code is None
    assert row.state_code in {
        f"S{n:02d}" for n in range(0, 15)
    }, f"unexpected state {row.state_code!r}"
    assert first["seeded"] >= 1
    assert first["dry_run"] is False

    # Idempotency: re-run should NOT add another row for this user.
    with app.app_context():
        second = backfill_all_users(commit=True)
    rows_after = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .count()
    )
    assert rows_after == 1, (
        f"backfill must be idempotent — expected 1 row after re-run, "
        f"got {rows_after}"
    )
    # The re-run reports the admin_user under already_have_history.
    assert second["already_have_history"] >= 1

    _purge_history(db_session, admin_user.id)


def test_backfill_dry_run_writes_no_rows(app, db_session, admin_user):
    """Dry-run must count but not write."""
    from fiesta.markov.backfill import backfill_all_users
    from fiesta.markov.models import UserStateHistory

    _purge_history(db_session, admin_user.id)

    with app.app_context():
        summary = backfill_all_users(commit=False)

    rows = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .count()
    )
    assert rows == 0, "dry-run must NOT write rows"
    assert summary["dry_run"] is True
    assert summary["seeded"] == 0
    assert summary["would_seed"] >= 1

    _purge_history(db_session, admin_user.id)


# --------------------------------------------------------------------------- #
# 5. Defer pattern: emit() doesn't block + doesn't raise even when the
#    state writer fails (catch + log)
# --------------------------------------------------------------------------- #

def test_emit_swallows_state_writer_failure(
    app, db_session, admin_user, monkeypatch
):
    """If the Markov state writer blows up for any reason, events.emit()
    must STILL return the Event.id (the Event row is the contract; the
    state-writer is opportunistic). NO exception propagates."""
    from events import emit
    from event_models import Event

    # Force EVENTS_SYNC_FOR_TEST=1 so the deferred path runs inline and
    # the assertion sees the result.
    monkeypatch.setenv("EVENTS_SYNC_FOR_TEST", "1")

    # Patch record_state_transition to raise. We patch the symbol at the
    # state_writer module path because events.emit imports it lazily
    # inside _write().
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated state-writer failure")

    import fiesta.markov.state_writer as sw
    monkeypatch.setattr(sw, "record_state_transition", _boom)

    _purge_history(db_session, admin_user.id)

    with app.test_request_context("/"):
        event_id = emit(
            "checkout_completed",
            user_id=admin_user.id,
            payload={"tier": "self_file"},
            source="test",
        )

    # The Event row MUST exist even though the state-writer raised.
    assert event_id is not None, "emit() should return Event.id even when state-writer fails"
    event = Event.query.get(event_id)
    assert event is not None
    assert event.event_type == "checkout_completed"
    assert event.user_id == admin_user.id

    # Cleanup the Event row (the fixture cleans the user; events for the
    # user would otherwise leak across runs).
    Event.query.filter(Event.user_id == admin_user.id).delete()
    db_session.commit()
    _purge_history(db_session, admin_user.id)


def test_emit_writes_state_history_row_on_transition_event(
    app, db_session, admin_user, monkeypatch
):
    """Happy path: emit() of a transition event creates BOTH an Event row
    AND a UserStateHistory row."""
    from events import emit
    from event_models import Event
    from fiesta.markov.models import UserStateHistory

    monkeypatch.setenv("EVENTS_SYNC_FOR_TEST", "1")
    _purge_history(db_session, admin_user.id)
    Event.query.filter(Event.user_id == admin_user.id).delete()
    db_session.commit()

    with app.test_request_context("/"):
        event_id = emit(
            "profile_complete",
            user_id=admin_user.id,
            payload={"trigger": "profile_save"},
            source="test",
        )

    assert event_id is not None
    history_rows = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .all()
    )
    assert len(history_rows) == 1, (
        f"expected 1 UserStateHistory row, got {len(history_rows)}"
    )
    assert history_rows[0].state_code == "S02"
    assert history_rows[0].trigger_event == "profile_complete"

    Event.query.filter(Event.user_id == admin_user.id).delete()
    db_session.commit()
    _purge_history(db_session, admin_user.id)


def test_emit_does_not_write_history_for_non_transition_event(
    app, db_session, admin_user, monkeypatch
):
    """emit() of a non-transition event creates ONLY an Event row;
    user_state_history is untouched."""
    from events import emit
    from event_models import Event
    from fiesta.markov.models import UserStateHistory

    monkeypatch.setenv("EVENTS_SYNC_FOR_TEST", "1")
    _purge_history(db_session, admin_user.id)
    Event.query.filter(Event.user_id == admin_user.id).delete()
    db_session.commit()

    with app.test_request_context("/"):
        event_id = emit(
            "nudge_sent",  # NOT a transition event
            user_id=admin_user.id,
            payload={"nudge": "tip_of_day"},
            source="test",
        )

    assert event_id is not None
    history_count = (
        UserStateHistory.query
        .filter(UserStateHistory.user_id == admin_user.id)
        .count()
    )
    assert history_count == 0, (
        f"non-transition event must NOT create a history row "
        f"(found {history_count})"
    )

    Event.query.filter(Event.user_id == admin_user.id).delete()
    db_session.commit()
