"""
Test fixtures for D2 support-ticket tests.

Re-uses the remittance-conftest scaffolding (env load, sys.path bootstrap,
app, db_session, login_as helper) for env + app bootstrap. Provides its OWN
user_a / user_b fixtures with UUID-suffix emails so a crashed prior run
can't block a fresh run with `UniqueViolation` on user.email.
"""
import uuid

import pytest

from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    login_as,
)


# --------------------------------------------------------------------------- #
# UUID-suffixed user fixtures — no collision with leftover pytest_user_a rows.
# --------------------------------------------------------------------------- #
def _make_unique_user(db_session, label: str):
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash

    suffix = uuid.uuid4().hex[:10]
    u = User(
        email=f"pytest_d2_{label}_{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest D2 {label} {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _teardown_user(db_session, user_id):
    """Remove a single pytest user + the FK rows we own. Bounded to user_id
    so we never touch other tests' data."""
    from sqlalchemy import text as _t
    for stmt in (
        "DELETE FROM d2_support_ticket_messages WHERE author_user_id = :uid",
        "DELETE FROM d2_support_ticket_messages "
        "WHERE ticket_id IN (SELECT id FROM d2_support_tickets WHERE user_id = :uid)",
        "DELETE FROM d2_support_tickets WHERE user_id = :uid",
        "DELETE FROM feedback WHERE user_id = :uid",
        "DELETE FROM audit_log WHERE user_id = :uid",
        'DELETE FROM "user" WHERE id = :uid',
    ):
        try:
            db_session.execute(_t(stmt), {"uid": user_id})
            db_session.commit()
        except Exception:
            try:
                db_session.rollback()
            except Exception:
                pass


@pytest.fixture
def user_a(db_session):
    u = _make_unique_user(db_session, "user_a")
    uid = u.id
    yield u
    _teardown_user(db_session, uid)


@pytest.fixture
def user_b(db_session):
    u = _make_unique_user(db_session, "user_b")
    uid = u.id
    yield u
    _teardown_user(db_session, uid)
