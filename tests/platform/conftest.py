"""Test fixtures for F-Platform-* regression tests.

Mirrors the pattern used by tests/persona/conftest.py and
tests/remittance/conftest.py — load fiesta.env, ensure sys.path, build
the Flask app in TESTING mode with all blueprints registered.

Provides factory-style user fixtures so the redirect-priority test can
construct users with arbitrary (persona, role, is_email_verified,
onboarding_completed, organizations) combinations without re-implementing
the cleanup dance in every test.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(_REPO_ROOT))


# Email prefix scoped to this suite so cleanup is safe + targeted.
PLATFORM_TEST_PREFIX = "pytest_platform_"


@pytest.fixture(scope="session")
def app():
    """Flask app in TESTING mode. Imports main so every blueprint registers
    (the redirect targets — remittance.dashboard, onboarding_wizard,
    verify_email_reminder — must all resolve via url_for)."""
    import main  # noqa: F401
    from app import app as flask_app, db
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        db.create_all()
    yield flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def db_session(app):
    from app import db as _db
    with app.app_context():
        yield _db.session
        _db.session.rollback()


def _make_user(
    db_session,
    suffix: str,
    *,
    persona: str | None = None,
    role: str = "user",
    is_email_verified: bool = True,
    onboarding_completed: bool = True,
    income_sources: list[str] | None = None,
):
    """Create a User row with the explicit profile attributes needed for
    redirect-priority assertions. Caller is responsible for adding orgs
    if the test requires them; default is zero orgs (the bug scenario).

    `income_sources` (MS4 W2 Agent 1, 2026-05-25): list of strings drawn
    from `INCOME_SOURCE_TYPES` (e.g. ['foreign_remittance', 'rsu']).
    Post-G1.2 the hub funnel-state recommender reads this column, so
    tests that assert hub funnel behaviour should set it explicitly.
    None → empty list (DB column default).
    """
    from models import User
    from werkzeug.security import generate_password_hash

    u = User(
        email=f"{PLATFORM_TEST_PREFIX}{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest Platform {suffix}",
        role=role,
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=is_email_verified,
        onboarding_completed=onboarding_completed,
        persona=persona,
        income_sources=list(income_sources or []),
    )
    db_session.add(u)
    db_session.commit()
    return u


def _cleanup_user(db_session, u):
    """Remove a test user + any FK-attached AuditLog rows. Avoid touching
    cascaded children that may not exist for the minimal test profile."""
    from models import AuditLog, User
    AuditLog.query.filter(AuditLog.user_id == u.id).delete(
        synchronize_session=False
    )
    User.query.filter(User.id == u.id).delete(synchronize_session=False)
    db_session.commit()


@pytest.fixture
def user_factory(db_session):
    """Build users on demand with arbitrary (persona, role, verified,
    onboarded) attributes. Tracks every created user + cleans them up
    at the end of the test, regardless of pass/fail."""
    created = []

    def _factory(
        suffix: str,
        *,
        persona: str | None = None,
        role: str = "user",
        is_email_verified: bool = True,
        onboarding_completed: bool = True,
        income_sources: list[str] | None = None,
    ):
        u = _make_user(
            db_session,
            suffix,
            persona=persona,
            role=role,
            is_email_verified=is_email_verified,
            onboarding_completed=onboarding_completed,
            income_sources=income_sources,
        )
        created.append(u)
        return u

    yield _factory

    for u in created:
        try:
            _cleanup_user(db_session, u)
        except Exception:
            # Best-effort cleanup; never let a teardown failure mask a real
            # test failure. The PLATFORM_TEST_PREFIX guarantees the rows
            # are identifiable in the DB if a manual sweep is needed.
            db_session.rollback()


@pytest.fixture(autouse=True)
def _cleanup_orphan_platform_users(db_session):
    """Belt-and-braces cleanup: at end of every test, sweep any rows whose
    email matches PLATFORM_TEST_PREFIX that the factory teardown missed
    (e.g. a test that committed an explicit User outside the factory)."""
    yield
    from models import AuditLog, User
    orphans = User.query.filter(
        User.email.like(f"{PLATFORM_TEST_PREFIX}%")
    ).all()
    if orphans:
        ids = [u.id for u in orphans]
        AuditLog.query.filter(AuditLog.user_id.in_(ids)).delete(
            synchronize_session=False
        )
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
        db_session.commit()


def login_as(client, user):
    """Bypass the email/password form by setting the Flask-Login session
    cookie directly. Mirrors tests/persona/conftest.py::login_as."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
