"""Tier D4 / A5 — Lifecycle email drip tests.

3 cases:
  1. _schedule_for produces correct timestamps for each (event, email_key)
     pair AND returns None for the no-op combinations.
  2. compose() returns subject + html for every EMAIL_KEYS entry, falls
     back gracefully when the template render fails.
  3. send() with stubbed delivery records sent_at + send_status='sent',
     and behavioural-skip for calculator_nudge when user already ran the
     calculator is wired correctly.

DB layer is fully stubbed via mocks so these run without a live Postgres
or migrations applied — mirrors tests/dunning/test_dunning_sequence.py.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# --------------------------------------------------------------------------- #
# Test 1: schedule helper coverage.
# --------------------------------------------------------------------------- #

def test_schedule_for_covers_all_pairs_and_no_ops():
    from lifecycle_drip import _schedule_for, _next_deadline

    base = datetime(2026, 5, 24, 12, 0, 0)  # fixed reference

    # signup event
    assert _schedule_for("welcome", "signup", now=base) == base
    assert (
        _schedule_for("calculator_nudge", "signup", now=base)
        == base + timedelta(days=1)
    )
    assert _schedule_for("payment_thanks", "signup", now=base) is None
    assert _schedule_for("sep30_30day", "signup", now=base) is None
    assert _schedule_for("nov30_30day", "signup", now=base) is None

    # payment_completed event
    assert (
        _schedule_for("payment_thanks", "payment_completed", now=base)
        == base
    )
    assert _schedule_for("welcome", "payment_completed", now=base) is None
    assert (
        _schedule_for("calculator_nudge", "payment_completed", now=base)
        is None
    )
    assert (
        _schedule_for("sep30_30day", "payment_completed", now=base) is None
    )

    # tax_year_cycle event — both deadline reminders fire 30d before
    sep30 = _schedule_for("sep30_30day", "tax_year_cycle", now=base)
    nov30 = _schedule_for("nov30_30day", "tax_year_cycle", now=base)
    assert sep30 is not None
    assert nov30 is not None
    # Compare against the next-deadline helper
    expected_sep = datetime.combine(
        _next_deadline(9, 30) - timedelta(days=30),
        datetime.min.time(),
    )
    expected_nov = datetime.combine(
        _next_deadline(11, 30) - timedelta(days=30),
        datetime.min.time(),
    )
    assert sep30 == expected_sep
    assert nov30 == expected_nov

    # Unknown event = no-op for any key
    for key in (
        "welcome", "calculator_nudge", "payment_thanks",
        "sep30_30day", "nov30_30day",
    ):
        assert _schedule_for(key, "garbage_event", now=base) is None


def test_next_deadline_rolls_into_next_year_after_date_passes():
    from lifecycle_drip import _next_deadline
    # If today is Oct 5, the next Sep 30 must be next year's.
    today = date(2026, 10, 5)
    assert _next_deadline(9, 30, today=today) == date(2027, 9, 30)
    # If today is Aug 1, the next Sep 30 is this year.
    assert _next_deadline(9, 30, today=date(2026, 8, 1)) == date(2026, 9, 30)


# --------------------------------------------------------------------------- #
# Test 2: compose() produces subject + html for every email_key.
# --------------------------------------------------------------------------- #

def test_compose_renders_every_email_key_with_fallback_on_render_error():
    from lifecycle_drip import compose
    from lifecycle_drip_models import EMAIL_KEYS

    assert len(EMAIL_KEYS) == 5  # council cap binding

    user = SimpleNamespace(email="t@example.com", name="Test User")

    # Force render_template to raise -> use inline fallback.
    with patch("lifecycle_drip.render_template",
               side_effect=RuntimeError("no app context in test")):
        for key in EMAIL_KEYS:
            out = compose(key, user, context={"user_name": "Test User"})
            assert out["to"] == "t@example.com"
            assert isinstance(out["subject"], str) and len(out["subject"]) > 5
            assert isinstance(out["html"], str) and "Test User" in out["html"]
            # Fallback body always has the subject as the H1
            assert out["subject"] in out["html"]

    with pytest.raises(ValueError):
        compose("not_a_real_key", user)


# --------------------------------------------------------------------------- #
# Test 3: send() flips state + behavioural skip for calculator_nudge.
# --------------------------------------------------------------------------- #

def test_send_marks_sent_and_skips_calculator_nudge_when_already_run():
    import lifecycle_drip as ld
    from lifecycle_drip_models import (
        STATUS_PENDING, STATUS_SENT, STATUS_SKIPPED,
    )

    # Pretend row in DB.
    row_sent = SimpleNamespace(
        id=101, user_id=42, email_key="welcome",
        cohort_id="2026-05", scheduled_at=datetime.utcnow(),
        sent_at=None, send_status=STATUS_PENDING,
        failure_reason=None, context_json=None,
    )
    row_nudge = SimpleNamespace(
        id=102, user_id=42, email_key="calculator_nudge",
        cohort_id="2026-05", scheduled_at=datetime.utcnow(),
        sent_at=None, send_status=STATUS_PENDING,
        failure_reason=None, context_json=None,
    )

    fake_user = SimpleNamespace(id=42, email="t@example.com", name="Test")

    # Patch DB session + User.query.get + compose + _send_stub +
    # _user_has_calculated. All side effects stubbed.
    fake_db = MagicMock()
    fake_db.session = MagicMock()
    fake_user_cls = MagicMock()
    fake_user_cls.query.get.return_value = fake_user

    with patch.dict(
            "sys.modules",
            {"app": MagicMock(db=fake_db),
             "models": MagicMock(User=fake_user_cls)}), \
        patch.object(ld, "compose", return_value={
                "to": "t@example.com", "subject": "x", "html": "<p>y</p>"}), \
        patch.object(ld, "_send_stub", return_value=(True, None)), \
        patch.object(ld, "_user_has_calculated", return_value=False):

        ok = ld.send(row_sent)

    assert ok is True
    assert row_sent.send_status == STATUS_SENT
    assert row_sent.sent_at is not None
    assert row_sent.failure_reason is None

    # Now: calculator_nudge for a user who has already calculated -> SKIPPED.
    with patch.dict(
            "sys.modules",
            {"app": MagicMock(db=fake_db),
             "models": MagicMock(User=fake_user_cls)}), \
        patch.object(ld, "_send_stub", return_value=(True, None)), \
        patch.object(ld, "_user_has_calculated", return_value=True):

        ok2 = ld.send(row_nudge)

    assert ok2 is False
    assert row_nudge.send_status == STATUS_SKIPPED
    assert "already" in (row_nudge.failure_reason or "").lower()


# --------------------------------------------------------------------------- #
# Test 4 (bonus): EMAIL_KEYS is locked at 5 (council cap).
# --------------------------------------------------------------------------- #

def test_email_keys_capped_at_five():
    from lifecycle_drip_models import EMAIL_KEYS
    assert len(EMAIL_KEYS) == 5
    # Set is binding — no dupes accidentally added.
    assert len(set(EMAIL_KEYS)) == 5
