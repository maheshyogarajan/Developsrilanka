"""
Fixtures for the Tier-C / Wave A analytics dashboard tests.

Mirrors the Wave 6 fiesta_admin fixture pattern (admin_user / non_admin_user
/ login_as) and the analytics-beacon cleanup_events fixture so a test can
seed `events` rows AND know they'll be cleaned up on teardown.

All fixtures speak to the live Neon DB (same pattern as the rest of the
suite). Users are prefixed `pytest_an_dash_` so concurrent runs and the
existing fiesta_admin prefix never collide.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

# Reuse the validated app + client + db_session fixtures from the remittance
# conftest (they handle .env loading + main import + CSRF disable).
from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
)
from tests.remittance.conftest import login_as as _login_as_helper


@pytest.fixture
def login_as():
    """Expose remittance.conftest.login_as(client, user) as a pytest fixture."""
    return _login_as_helper


# Suite-local user prefix — does not collide with the fiesta_admin suite.
DASH_TEST_PREFIX = "pytest_an_dash_"


def _make_user(*, db_session, is_admin: bool = False):
    """Create a User row and return it. Caller owns teardown via
    _cleanup_user / _cleanup_orphan_dash_users below."""
    from models import User
    email = f"{DASH_TEST_PREFIX}{uuid.uuid4().hex[:8]}@fiesta.local"
    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest Analytics {email[:20]}",
        role="admin" if is_admin else "user",
        subscription_status="self_file" if is_admin else "free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
        tos_accepted_version="v0.1-draft",
        tos_accepted_at=datetime.utcnow(),
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def admin_user(db_session):
    u = _make_user(db_session=db_session, is_admin=True)
    yield u
    _cleanup_user(db_session, u.id)


@pytest.fixture
def non_admin_user(db_session):
    u = _make_user(db_session=db_session, is_admin=False)
    yield u
    _cleanup_user(db_session, u.id)


def _cleanup_user(db_session, user_id: int) -> None:
    from models import User, AuditLog
    try:
        AuditLog.query.filter(AuditLog.user_id == user_id).delete(
            synchronize_session=False
        )
    except Exception:
        db_session.rollback()
    try:
        User.query.filter(User.id == user_id).delete(synchronize_session=False)
    except Exception:
        db_session.rollback()
    db_session.commit()


@pytest.fixture(autouse=True)
def _cleanup_orphan_dash_users(db_session):
    """Belt-and-braces sweep for prefix-matching rows leaked by crashed runs."""
    from models import User, AuditLog
    yield
    leftovers = User.query.filter(
        User.email.like(f"{DASH_TEST_PREFIX}%")
    ).all()
    if leftovers:
        ids = [u.id for u in leftovers]
        try:
            AuditLog.query.filter(AuditLog.user_id.in_(ids)).delete(
                synchronize_session=False
            )
        except Exception:
            db_session.rollback()
        try:
            User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
        except Exception:
            db_session.rollback()
        db_session.commit()


@pytest.fixture
def cleanup_events(app):
    """Track event rows created during a single test + delete on teardown.

    Returns a small helper object the test can call:
        cleanup_events.mark(event_id) -> register a row for cleanup
    Anything written during the test that doesn't go through .mark() is
    still cleaned up by the post-yield sweep (we snapshot the id set before
    the test runs).
    """
    from app import db
    from event_models import Event

    class _Tracker:
        def __init__(self):
            self.tracked_ids = set()
        def mark(self, event_id):
            if event_id is not None:
                self.tracked_ids.add(event_id)

    with app.app_context():
        before_ids = {r.id for r in Event.query.with_entities(Event.id).all()}
    tracker = _Tracker()
    yield tracker
    with app.app_context():
        new_ids = [
            r.id for r in Event.query.with_entities(Event.id).all()
            if r.id not in before_ids
        ] + list(tracker.tracked_ids)
        new_ids = list({i for i in new_ids if i is not None})
        if new_ids:
            Event.query.filter(Event.id.in_(new_ids)).delete(synchronize_session=False)
            db.session.commit()


@pytest.fixture
def seed_funnel_events(app, cleanup_events):
    """Seed a deterministic funnel sample so the dashboard has rows to render
    + tally. Returns the (anon_id_a, anon_id_b, channel_a, channel_b) tuple
    so tests can assert against known values.

    Layout:
      - 'lanka_devs' channel: 1 anon completes landing -> signup_started
        -> signup_completed -> payment_completed (the full funnel).
      - 'fb_freelancers' channel: 1 anon lands only.
      - Both writes go through the live `events` table; cleanup_events
        will sweep them on teardown.
    """
    from app import db
    from event_models import Event

    anon_a = f"dash_seed_a_{uuid.uuid4().hex[:8]}"
    anon_b = f"dash_seed_b_{uuid.uuid4().hex[:8]}"
    channel_a = "lanka_devs"
    channel_b = "fb_freelancers"

    rows = []
    with app.app_context():
        # Full funnel for anon_a / lanka_devs.
        for ev in ("landing_view", "signup_started",
                   "signup_completed", "payment_completed"):
            e = Event(
                event_type=ev,
                payload={"session_anon_id": anon_a, "utm_source": channel_a},
                source="beacon",
                session_anon_id=anon_a,
            )
            db.session.add(e)
            rows.append(e)

        # Landing only for anon_b / fb_freelancers.
        e = Event(
            event_type="landing_view",
            payload={"session_anon_id": anon_b, "utm_source": channel_b},
            source="beacon",
            session_anon_id=anon_b,
        )
        db.session.add(e)
        rows.append(e)

        db.session.commit()
        for r in rows:
            cleanup_events.mark(r.id)

    return {
        "anon_a": anon_a,
        "anon_b": anon_b,
        "channel_a": channel_a,
        "channel_b": channel_b,
    }
