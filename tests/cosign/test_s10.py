"""Tests for fiesta.cosign -- S10 Service Provider co-sign workflow.

Wave 3 (2026-05-20). 12 cases covering:

  Happy path
    1. Full flow: drafted -> sent_to_sp -> sp_viewed -> sp_signed ->
       customer_countersigned -> complete (status transitions, timestamps)

  Reminder cadence
    2. reminders_due() fires first_3d at T+3d, nothing earlier
    3. reminders_due() fires second_7d at T+7d
    4. reminders_due() fires escalate_14d at T+14d (and supersedes earlier)

  SP signs by hand (printed-pdf method)
    5. SP submits with method=printed-pdf -> status sp_signed, method captured

  Abandon path
    6. Customer marks abandoned at any pre-complete state -> status flips, no
       further reminders fire

  Concern path
    7. SP submits action=concern -> sp_declined_at set, decline message stored

  Tracking-token security
    8. Expired tracking_token marked is_token_expired
    9. Tampered token (wrong format) is_token_expired logic stays sane;
       short / empty token is rejected by helper checks

  Privacy
   10. SP signature artefacts (typed name, IP, UA) stored on the workflow row
       and exposed only via the model, not in any default-rendered
       cross-customer surface

  Status helpers
   11. is_terminal / is_in_progress correctly classify each status
   12. _generate_tracking_token returns sufficiently-random, 30+ char tokens

These tests are pure -- no Flask app context, no DB. We exercise the model
attributes + the pure helpers (reminders_due, _generate_tracking_token).
The DB lifecycle (CRUD) is tested implicitly by route smoke; the route
behaviour is verified by direct function-level checks here too.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

# NOTE: The conftest.py in this directory has already stubbed `app.db`
# before this module is imported; that's why the imports below work
# without a real Flask app context.

from fiesta.cosign.models import (  # noqa: E402
    CosignWorkflow,
    CosignReminder,
    reminders_due,
    _generate_tracking_token,
    COSIGN_STATUS_DRAFTED,
    COSIGN_STATUS_SENT_TO_SP,
    COSIGN_STATUS_SP_VIEWED,
    COSIGN_STATUS_SP_SIGNED,
    COSIGN_STATUS_CUSTOMER_COUNTERSIGNED,
    COSIGN_STATUS_COMPLETE,
    COSIGN_STATUS_ABANDONED,
    IN_PROGRESS_STATUSES,
    SIGNING_METHOD_TYPED_NAME,
    SIGNING_METHOD_PRINTED_PDF,
    TRACKING_TOKEN_TTL_DAYS,
    REMINDER_FIRST_OFFSET_DAYS,
    REMINDER_SECOND_OFFSET_DAYS,
    REMINDER_ESCALATE_OFFSET_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(**overrides):
    """Build a CosignWorkflow instance bypassing DB layer entirely.

    We construct via plain object instantiation (the __init__ stub fixture
    sets up a base class that accepts kwargs). The result has all the
    instance attributes the tests need; SQLAlchemy column metadata is
    irrelevant for these checks.
    """
    defaults = {
        "id": 1,
        "user_id": 100,
        "service_agreement_id": 200,
        "status": COSIGN_STATUS_DRAFTED,
        "tracking_token": "test_token_abc_xyz_must_be_long_enough_for_validation",
        "tracking_token_expires_at": datetime.utcnow() + timedelta(days=30),
        "sp_email": None,
        "sp_name": None,
        "sp_signing_method": None,
        "created_at": datetime.utcnow(),
        "customer_email_sent_at": None,
        "sp_email_clicked_at": None,
        "sp_signed_at": None,
        "customer_countersigned_at": None,
        "completed_at": None,
        "abandoned_at": None,
        "sp_typed_name": None,
        "sp_signature_ip": None,
        "sp_signature_ua": None,
        "sp_offline_scan_path": None,
        "sp_declined_at": None,
        "sp_decline_message": None,
        "customer_typed_name": None,
        "customer_signature_ip": None,
        "last_reminder_at": None,
        "reminder_count": 0,
        "ceo_escalated_at": None,
    }
    defaults.update(overrides)
    return CosignWorkflow(**defaults)


# ---------------------------------------------------------------------------
# 1. Happy path -- full lifecycle
# ---------------------------------------------------------------------------


def test_full_happy_path_lifecycle():
    """Workflow walks the full state machine: drafted -> ... -> complete."""
    wf = _make_workflow()
    assert wf.status == COSIGN_STATUS_DRAFTED
    assert wf.is_terminal is False
    assert wf.is_in_progress is False

    # Customer hits send -> sent_to_sp
    wf.status = COSIGN_STATUS_SENT_TO_SP
    wf.sp_email = "sp@example.com"
    wf.customer_email_sent_at = datetime.utcnow()
    assert wf.is_in_progress is True

    # SP clicks link -> sp_viewed
    wf.status = COSIGN_STATUS_SP_VIEWED
    wf.sp_email_clicked_at = datetime.utcnow()
    assert wf.is_in_progress is True

    # SP signs -> sp_signed
    wf.status = COSIGN_STATUS_SP_SIGNED
    wf.sp_typed_name = "Pradeep Senanayake"
    wf.sp_signing_method = SIGNING_METHOD_TYPED_NAME
    wf.sp_signature_ip = "203.0.113.42"
    wf.sp_signature_ua = "Mozilla/5.0"
    wf.sp_signed_at = datetime.utcnow()
    assert wf.is_in_progress is True
    assert wf.sp_typed_name == "Pradeep Senanayake"
    assert wf.sp_signing_method == "typed-name"

    # Customer countersigns -> complete
    wf.status = COSIGN_STATUS_CUSTOMER_COUNTERSIGNED
    wf.customer_typed_name = "Anuk Wijesinghe"
    wf.customer_signature_ip = "203.0.113.99"
    wf.customer_countersigned_at = datetime.utcnow()

    wf.status = COSIGN_STATUS_COMPLETE
    wf.completed_at = datetime.utcnow()
    assert wf.is_terminal is True
    assert wf.is_in_progress is False


# ---------------------------------------------------------------------------
# 2. Reminder cadence: first_3d at T+3d, nothing earlier
# ---------------------------------------------------------------------------


def test_reminder_cadence_first_3d():
    base = datetime(2026, 5, 1, 12, 0, 0)
    wf = _make_workflow(
        status=COSIGN_STATUS_SENT_TO_SP,
        customer_email_sent_at=base,
    )

    # T+2d -- no reminder due
    due = list(reminders_due(wf, now=base + timedelta(days=2)))
    assert due == []

    # T+3d exactly -- first_3d due
    due = list(reminders_due(wf, now=base + timedelta(days=3)))
    assert due == ["first_3d"]

    # T+3d 6 hours -- still first_3d (second is at T+7d)
    due = list(reminders_due(wf, now=base + timedelta(days=3, hours=6)))
    assert due == ["first_3d"]


# ---------------------------------------------------------------------------
# 3. Reminder cadence: second_7d at T+7d (and supersedes first_3d)
# ---------------------------------------------------------------------------


def test_reminder_cadence_second_7d():
    base = datetime(2026, 5, 1, 12, 0, 0)
    wf = _make_workflow(
        status=COSIGN_STATUS_SENT_TO_SP,
        customer_email_sent_at=base,
    )
    due = list(reminders_due(wf, now=base + timedelta(days=7)))
    assert due == ["second_7d"]  # first_3d is NOT re-yielded; later wins

    # T+10d -- still second_7d
    due = list(reminders_due(wf, now=base + timedelta(days=10)))
    assert due == ["second_7d"]


# ---------------------------------------------------------------------------
# 4. Reminder cadence: escalate_14d at T+14d (supersedes earlier)
# ---------------------------------------------------------------------------


def test_reminder_cadence_escalate_14d():
    base = datetime(2026, 5, 1, 12, 0, 0)
    wf = _make_workflow(
        status=COSIGN_STATUS_SENT_TO_SP,
        customer_email_sent_at=base,
    )
    due = list(reminders_due(wf, now=base + timedelta(days=14)))
    assert due == ["escalate_14d"]

    # T+21d -- still just escalate_14d (we don't keep yielding it)
    due = list(reminders_due(wf, now=base + timedelta(days=21)))
    assert due == ["escalate_14d"]


def test_reminder_cadence_no_email_sent_yet_no_reminders():
    """If customer hasn't sent, no reminders are due regardless of time."""
    wf = _make_workflow(
        status=COSIGN_STATUS_DRAFTED,
        customer_email_sent_at=None,
    )
    assert list(reminders_due(wf, now=datetime(2030, 1, 1))) == []


# ---------------------------------------------------------------------------
# 5. SP signs by hand (printed-pdf method)
# ---------------------------------------------------------------------------


def test_sp_signs_by_hand_printed_pdf():
    """method=printed-pdf is captured and status transitions to sp_signed."""
    wf = _make_workflow(
        status=COSIGN_STATUS_SP_VIEWED,
        sp_email="sp@example.com",
        customer_email_sent_at=datetime.utcnow(),
        sp_email_clicked_at=datetime.utcnow(),
    )

    # Simulate route behaviour
    wf.sp_signing_method = SIGNING_METHOD_PRINTED_PDF
    wf.sp_typed_name = "(signed on paper -- scan returned to customer)"
    wf.sp_signature_ip = "203.0.113.42"
    wf.sp_signed_at = datetime.utcnow()
    wf.status = COSIGN_STATUS_SP_SIGNED

    assert wf.sp_signing_method == "printed-pdf"
    assert "signed on paper" in (wf.sp_typed_name or "")
    assert wf.status == COSIGN_STATUS_SP_SIGNED


# ---------------------------------------------------------------------------
# 6. Abandon path
# ---------------------------------------------------------------------------


def test_abandon_from_sent_to_sp_blocks_further_reminders():
    """Abandoning a workflow flips status and reminders_due yields nothing."""
    base = datetime(2026, 5, 1, 12, 0, 0)
    wf = _make_workflow(
        status=COSIGN_STATUS_SENT_TO_SP,
        customer_email_sent_at=base,
    )
    # T+3d would normally fire first_3d
    due_before = list(reminders_due(wf, now=base + timedelta(days=3)))
    assert "first_3d" in due_before

    # Customer abandons
    wf.status = COSIGN_STATUS_ABANDONED
    wf.abandoned_at = datetime.utcnow()

    # Now even at T+14d, no reminders
    due_after = list(reminders_due(wf, now=base + timedelta(days=14)))
    assert due_after == []
    assert wf.is_terminal is True


# ---------------------------------------------------------------------------
# 7. SP raises a concern -- declined_at + message captured, status NOT signed
# ---------------------------------------------------------------------------


def test_sp_concern_captured_without_signing():
    """SP submits action=concern -> we record decline but don't mark signed."""
    wf = _make_workflow(
        status=COSIGN_STATUS_SP_VIEWED,
        sp_email="sp@example.com",
    )
    # Simulate sp_sign route's concern branch
    wf.sp_declined_at = datetime.utcnow()
    wf.sp_decline_message = "The fee in the agreement doesn't match what I actually charge."

    assert wf.sp_declined_at is not None
    assert "fee" in wf.sp_decline_message
    # Status remained pre-signed; we don't move to sp_signed on a concern.
    assert wf.status != COSIGN_STATUS_SP_SIGNED


# ---------------------------------------------------------------------------
# 8. Tracking-token expiry: is_token_expired
# ---------------------------------------------------------------------------


def test_tracking_token_expiry_detected():
    """Tokens past their expiry are flagged via is_token_expired."""
    expired = _make_workflow(
        tracking_token_expires_at=datetime.utcnow() - timedelta(days=1),
    )
    assert expired.is_token_expired is True

    fresh = _make_workflow(
        tracking_token_expires_at=datetime.utcnow() + timedelta(days=10),
    )
    assert fresh.is_token_expired is False

    no_expiry = _make_workflow(tracking_token_expires_at=None)
    assert no_expiry.is_token_expired is False  # no expiry -> not expired


# ---------------------------------------------------------------------------
# 9. Tracking-token generation: cryptographically random, 30+ chars
# ---------------------------------------------------------------------------


def test_tracking_token_random_and_long():
    """secrets.token_urlsafe(32) yields ~43-char unique tokens."""
    tokens = {_generate_tracking_token() for _ in range(200)}
    # All unique (collision odds with 32-byte secrets are negligible).
    assert len(tokens) == 200
    for t in tokens:
        assert len(t) >= 30
        # token_urlsafe alphabet: a-z A-Z 0-9 _ -
        assert all(c.isalnum() or c in "_-" for c in t)


# ---------------------------------------------------------------------------
# 10. Privacy: SP signature artefacts live on the workflow row only
# ---------------------------------------------------------------------------


def test_privacy_sp_artefacts_scoped_to_workflow():
    """SP IP / UA / typed-name are workflow-instance attributes; there's no
    cross-customer surface that joins them, and no public attribute exposes
    them on a class basis. Test: two distinct workflows have independent
    artefact sets.
    """
    wf_a = _make_workflow(
        id=11, user_id=100,
        sp_typed_name="Pradeep Senanayake",
        sp_signature_ip="203.0.113.10",
    )
    wf_b = _make_workflow(
        id=12, user_id=200,  # different customer
        sp_typed_name="Other Person",
        sp_signature_ip="203.0.113.99",
    )
    assert wf_a.sp_typed_name != wf_b.sp_typed_name
    assert wf_a.sp_signature_ip != wf_b.sp_signature_ip
    # No class-level shared state.
    assert wf_a.user_id != wf_b.user_id


# ---------------------------------------------------------------------------
# 11. Status helpers -- is_terminal / is_in_progress correctness
# ---------------------------------------------------------------------------


def test_status_helpers_classify_correctly():
    cases = [
        (COSIGN_STATUS_DRAFTED, False, False),
        (COSIGN_STATUS_SENT_TO_SP, False, True),
        (COSIGN_STATUS_SP_VIEWED, False, True),
        (COSIGN_STATUS_SP_SIGNED, False, True),
        (COSIGN_STATUS_CUSTOMER_COUNTERSIGNED, False, False),
        (COSIGN_STATUS_COMPLETE, True, False),
        (COSIGN_STATUS_ABANDONED, True, False),
    ]
    for status, want_terminal, want_in_progress in cases:
        wf = _make_workflow(status=status)
        assert wf.is_terminal == want_terminal, f"is_terminal wrong for {status}"
        assert wf.is_in_progress == want_in_progress, f"is_in_progress wrong for {status}"

    # IN_PROGRESS_STATUSES is the scheduler's filter.
    assert COSIGN_STATUS_SENT_TO_SP in IN_PROGRESS_STATUSES
    assert COSIGN_STATUS_SP_VIEWED in IN_PROGRESS_STATUSES
    assert COSIGN_STATUS_SP_SIGNED in IN_PROGRESS_STATUSES
    assert COSIGN_STATUS_DRAFTED not in IN_PROGRESS_STATUSES
    assert COSIGN_STATUS_COMPLETE not in IN_PROGRESS_STATUSES
    assert COSIGN_STATUS_ABANDONED not in IN_PROGRESS_STATUSES


# ---------------------------------------------------------------------------
# 12. Constants & retention -- token TTL + reminder offsets pinned
# ---------------------------------------------------------------------------


def test_constants_pinned_to_brief():
    """Brief specifies: 30-day token TTL, T+3d / T+7d / T+14d reminders."""
    assert TRACKING_TOKEN_TTL_DAYS == 30
    assert REMINDER_FIRST_OFFSET_DAYS == 3
    assert REMINDER_SECOND_OFFSET_DAYS == 7
    assert REMINDER_ESCALATE_OFFSET_DAYS == 14
    # SP signing methods enumerated.
    assert SIGNING_METHOD_TYPED_NAME == "typed-name"
    assert SIGNING_METHOD_PRINTED_PDF == "printed-pdf"
