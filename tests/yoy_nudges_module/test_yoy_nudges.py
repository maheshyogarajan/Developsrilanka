"""
Tier D4 / C2 — YoY retention nudge tests.

Three cases (matches the brief minimum of 2-3):

  1. test_apr_1_schedules_for_paid_user_and_is_idempotent
       — paid user with a Subscription row gets exactly one apr_1 row
         on first schedule_apr_1_nudges() call, and a second call does
         NOT duplicate (dedup_key UNIQUE).

  2. test_renewal_nudge_fires_only_for_expiring_subscriptions
       — user with a sub expiring in 15 days IS scheduled; user with a
         sub expiring in 120 days is NOT.

  3. test_dispatch_marks_pending_rows_as_stubbed
       — scheduling then dispatching transitions send_status from
         'scheduled' -> 'stubbed' and stamps sent_at.
"""
from __future__ import annotations

import pytest

from yoy_nudges import (
    NUDGE_APR_1,
    NUDGE_RENEWAL,
    schedule_apr_1_nudges,
    schedule_renewal_nudges,
    dispatch_pending,
)


def test_apr_1_schedules_for_paid_user_and_is_idempotent(
    user_y, subscription_factory_y,
):
    # Arrange: user has an active subscription.
    subscription_factory_y(user_y, days_until_expiry=200)

    # Act 1: first schedule run.
    r1 = schedule_apr_1_nudges()

    # Assert: at least one row created for our user.
    from yoy_models import get_model
    YoYNudge = get_model()
    rows = YoYNudge.query.filter_by(
        user_id=user_y.id, nudge_key=NUDGE_APR_1,
    ).all()
    assert len(rows) == 1, (
        f"Expected exactly 1 apr_1 row for user {user_y.id}, got {len(rows)}"
    )
    assert rows[0].send_status == "scheduled"
    assert rows[0].sent_at is None

    # Act 2: second schedule run — should be a no-op for our user.
    schedule_apr_1_nudges()
    rows2 = YoYNudge.query.filter_by(
        user_id=user_y.id, nudge_key=NUDGE_APR_1,
    ).all()
    assert len(rows2) == 1, (
        "Idempotency broken: second schedule_apr_1_nudges() duplicated rows"
    )

    # Sanity on the helper return shape (audience may include other test users
    # on the same shared DB, so we don't assert exact counts).
    assert r1["nudge_key"] == NUDGE_APR_1
    assert r1["scheduled"] >= 1


def test_renewal_nudge_fires_only_for_expiring_subscriptions(
    user_y, subscription_factory_y, db_session,
):
    # Arrange: this user's subscription expires in 120 days — NOT in window.
    subscription_factory_y(user_y, days_until_expiry=120)

    # Act: renewal check.
    schedule_renewal_nudges()

    # Assert: no renewal row for this user.
    from yoy_models import get_model
    YoYNudge = get_model()
    rows = YoYNudge.query.filter_by(
        user_id=user_y.id, nudge_key=NUDGE_RENEWAL,
    ).all()
    assert len(rows) == 0, (
        f"Renewal nudge wrongly scheduled for user expiring in 120 days "
        f"(got {len(rows)} rows)"
    )

    # Now create a second user whose sub IS in the 30-day window.
    import uuid
    from tests.remittance.conftest import _make_user
    u2 = _make_user(db_session, f"yoy_exp_{uuid.uuid4().hex[:8]}")
    try:
        subscription_factory_y(u2, days_until_expiry=15)
        schedule_renewal_nudges()
        rows2 = YoYNudge.query.filter_by(
            user_id=u2.id, nudge_key=NUDGE_RENEWAL,
        ).all()
        assert len(rows2) == 1, (
            f"Renewal nudge should fire for user expiring in 15 days "
            f"(got {len(rows2)} rows)"
        )
    finally:
        # cleanup u2 (user_y autouse fixture cleans itself)
        try:
            from fiesta.paywall import get_models
            Subscription, _, _ = get_models()
            from yoy_models import get_model
            from models import User
            from app import db
            YoYNudge = get_model()
            YoYNudge.query.filter(YoYNudge.user_id == u2.id).delete(
                synchronize_session=False,
            )
            Subscription.query.filter(Subscription.user_id == u2.id).delete(
                synchronize_session=False,
            )
            User.query.filter(User.id == u2.id).delete()
            db.session.commit()
        except Exception:
            from app import db
            db.session.rollback()


def test_dispatch_marks_pending_rows_as_stubbed(
    user_y, subscription_factory_y,
):
    # Arrange: schedule one apr_1 row.
    subscription_factory_y(user_y, days_until_expiry=200)
    schedule_apr_1_nudges()

    from yoy_models import get_model
    YoYNudge = get_model()
    pre = YoYNudge.query.filter_by(user_id=user_y.id).first()
    assert pre is not None and pre.send_status == "scheduled"
    assert pre.sent_at is None

    # Act: dispatch.
    summary = dispatch_pending(limit=100)
    assert summary["sent"] >= 1
    assert summary["errors"] == 0

    # Assert: our row is now 'stubbed' with sent_at populated.
    post = YoYNudge.query.filter_by(id=pre.id).first()
    assert post is not None
    assert post.send_status == "stubbed", (
        f"Expected send_status=stubbed, got {post.send_status!r} "
        f"(send_error={post.send_error!r})"
    )
    assert post.sent_at is not None
