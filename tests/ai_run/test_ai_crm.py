"""
AI CRM / Customer Memory tests — Wave 2.3 brain.

Validates the per-user recompute path + heuristic scorers + admin gate:

  1. recompute_profile creates a row with sensible defaults for a cold user
  2. score_risk is HIGH (>60) for an inactive user (last_event_at 90d ago)
  3. pick_next_best_action returns ('complete_signup', ...) for a brand-new
     signup who hasn't set a persona yet
  4. aggregate_user_timeline orders events newest-first
  5. /admin/customer/<id> returns 403 for a non-admin user

Fixtures (app, db_session, user_a, user_b, admin_user, login_as) come from
tests/ai_run/conftest.py, which re-uses tests/remittance/conftest.py + adds
admin_user + ensures the customer_brain blueprint is registered.

DB hygiene note: tests run against the live Neon DB (council #1 decision —
real schema, real FK behaviour, real index plans). Each test creates fresh
user_a/user_b rows; the user_a/user_b fixtures handle teardown. We add an
autouse `_purge_ai_crm_rows` fixture so that even if a test errors before
its inline cleanup runs, the CustomerProfile + Event rows for that user are
removed before the user fixture's teardown tries to delete the user (the
profile FK has ON DELETE CASCADE, so this is defence-in-depth + faster
diagnostics on failure).
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from tests.ai_run.conftest import login_as


# --------------------------------------------------------------------------- #
# Hygiene — clean any leftover pytest CustomerProfile/Event rows
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _purge_ai_crm_rows(app, db_session):
    """Before AND after each test, sweep any CustomerProfile / Event rows
    belonging to the pytest user accounts (pytest_*@fiesta.local).

    Runs even if the test errors mid-body, because pytest fixture teardown
    is always invoked. Without this, a failed assertion would leave a
    CustomerProfile FK that blocks the user_a/user_b fixture's DELETE FROM
    user → next test's INSERT INTO user fails with UniqueViolation.
    """
    def _sweep():
        from sqlalchemy import text as _t
        try:
            with app.app_context():
                # Find pytest user ids
                ids = [
                    r[0] for r in db_session.execute(
                        _t("""SELECT id FROM "user"
                              WHERE email LIKE 'pytest_%@fiesta.local'""")
                    ).fetchall()
                ]
                if not ids:
                    return
                # NB: customer_profiles has ON DELETE CASCADE, so this is
                # not strictly needed, but doing it explicitly keeps the
                # error path readable.
                for tbl in ("customer_profiles", "events"):
                    try:
                        db_session.execute(
                            _t(f"DELETE FROM {tbl} WHERE user_id = ANY(:ids)"),
                            {"ids": ids},
                        )
                    except Exception:
                        db_session.rollback()
                db_session.commit()
        except Exception:
            try:
                db_session.rollback()
            except Exception:
                pass

    _sweep()
    yield
    _sweep()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _seed_event(db_session, user_id, event_type, created_at=None):
    """Insert one Event row directly (bypassing emit() so we control timestamp)."""
    from event_models import Event
    ev = Event(
        event_type=event_type,
        user_id=user_id,
        payload={},
        source="test",
    )
    if created_at is not None:
        ev.created_at = created_at
    db_session.add(ev)
    db_session.commit()
    return ev


# --------------------------------------------------------------------------- #
# 1. recompute creates a row with sensible defaults
# --------------------------------------------------------------------------- #

def test_recompute_profile_creates_row(app, db_session, user_a):
    """A user with zero events / zero remittances must still get a CustomerProfile
    row on first recompute, populated with default values."""
    from ai_crm import recompute_profile, CustomerProfile

    with app.app_context():
        profile = recompute_profile(user_a.id)

        assert profile is not None, "recompute_profile must return the row"
        assert profile.user_id == user_a.id

        # Default-y values for a cold user
        assert profile.lifecycle_stage == "signup", (
            f"Cold user with no events should be 'signup', got {profile.lifecycle_stage!r}"
        )
        assert profile.lifetime_remittance_count == 0
        assert Decimal(str(profile.lifetime_remittance_lkr)) == Decimal("0")
        assert profile.last_event_at is None
        assert profile.last_remittance_at is None
        assert profile.first_seen_at is not None
        assert profile.last_recomputed_at is not None

        # Row is queryable independent of the returned reference
        fetched = (
            CustomerProfile.query
                           .filter(CustomerProfile.user_id == user_a.id)
                           .first()
        )
        assert fetched is not None
        assert fetched.id == profile.id


# --------------------------------------------------------------------------- #
# 2. risk score HIGH for inactive user
# --------------------------------------------------------------------------- #

def test_risk_score_inactive_user_high(app, db_session, user_a):
    """A user whose last event was 90 days ago should score > 60 (high churn risk).

    Heuristic components that fire for this case:
      - days_since_event > DORMANT_DAYS (30)  →  +40 (RISK_CAP_DAYS_SINCE_EVENT)
      - persona='sl_foreign_income', no remittance ever → +30 (RISK_CAP_DAYS_SINCE_REMIT)
      - lifecycle_stage = 'dormant'           →  +20 (RISK_CAP_LIFECYCLE)
    Total: 90. Well above the >60 threshold the test asserts.
    """
    from ai_crm import score_risk

    with app.app_context():
        _seed_event(
            db_session, user_a.id, "signup",
            created_at=datetime.utcnow() - timedelta(days=90),
        )

        risk = score_risk(user_a.id)

        assert risk > 60, (
            f"User inactive 90d (last event {datetime.utcnow() - timedelta(days=90):%Y-%m-%d}) "
            f"should score > 60, got {risk}"
        )
        assert risk <= 100, f"Risk score must be capped at 100, got {risk}"


# --------------------------------------------------------------------------- #
# 3. NBA for a brand-new signup with no persona
# --------------------------------------------------------------------------- #

def test_pick_next_best_action_for_new_signup(app, db_session, user_b):
    """A user with ONLY a 'signup' event (no persona_set, no User.persona) →
    NBA must be 'complete_signup'."""
    from ai_crm import pick_next_best_action

    with app.app_context():
        # Force persona NULL via raw UPDATE so the next SELECT in
        # _user_persona_and_subscription() reads NULL from the DB rather than
        # a potentially-cached attribute on the in-session object.
        from sqlalchemy import text as _t
        db_session.execute(
            _t('UPDATE "user" SET persona = NULL WHERE id = :uid'),
            {"uid": user_b.id},
        )
        db_session.commit()
        db_session.expire_all()

        _seed_event(db_session, user_b.id, "signup")

        action, reason = pick_next_best_action(user_b.id)

        assert action == "complete_signup", (
            f"Brand-new signup (no persona) should NBA = 'complete_signup', got {action!r} "
            f"(reason: {reason!r})"
        )
        assert "persona" in reason.lower(), (
            f"Reason should mention persona, got {reason!r}"
        )


# --------------------------------------------------------------------------- #
# 4. Timeline orders newest-first
# --------------------------------------------------------------------------- #

def test_aggregate_timeline_orders_desc(app, db_session, user_a):
    """Events seeded out-of-order must come back in descending created_at order."""
    from ai_crm import aggregate_user_timeline

    with app.app_context():
        now = datetime.utcnow()
        # Seed in middle-old-new order to prove sort is applied
        _seed_event(db_session, user_a.id, "persona_set", created_at=now - timedelta(days=3))
        _seed_event(db_session, user_a.id, "signup", created_at=now - timedelta(days=10))
        _seed_event(db_session, user_a.id, "remittance_added", created_at=now - timedelta(hours=1))

        timeline = aggregate_user_timeline(user_a.id)

        # We expect 3 items minimum — there shouldn't be other rows because the
        # user_a fixture creates a fresh user per test.
        event_items = [it for it in timeline if it["type"] == "event"]
        assert len(event_items) >= 3, (
            f"Should see all 3 seeded events in timeline, got {len(event_items)}"
        )

        # Newest-first ordering — strict monotonicity on the seeded subset
        seeded_summaries = ["remittance_added", "persona_set", "signup"]
        actual_summaries = [it["summary"] for it in event_items if it["summary"] in seeded_summaries]
        assert actual_summaries == seeded_summaries, (
            f"Timeline must order events newest-first. Expected {seeded_summaries}, "
            f"got {actual_summaries}"
        )

        # And the timestamps themselves are non-increasing
        timestamps = [it["at"] for it in timeline if it["at"] is not None]
        for earlier, later in zip(timestamps[1:], timestamps[:-1]):
            assert later >= earlier, (
                f"Timeline order broken: {later} should be >= {earlier}"
            )


# --------------------------------------------------------------------------- #
# 5. Admin gate — non-admin gets 403
# --------------------------------------------------------------------------- #

def test_admin_customer_view_requires_admin(client, db_session, user_a, user_b):
    """user_b (role='user') tries to view /admin/customer/<user_a.id>.
    Must return 403. Admin-only routes use inline abort(403) not redirect.
    """
    # Sanity: ensure user_b is NOT an admin
    user_b.role = "user"
    db_session.commit()

    login_as(client, user_b)
    resp = client.get(f"/admin/customer/{user_a.id}")
    assert resp.status_code == 403, (
        f"Non-admin must get 403 on /admin/customer/<id>, got {resp.status_code}. "
        f"(user_b.role={user_b.role!r})"
    )
