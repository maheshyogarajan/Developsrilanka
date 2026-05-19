"""Fixtures for the S4 earnings tests.

Patterns borrowed from tests/remittance/conftest.py — loads the prod env, imports
`main` so all blueprints register, creates a fresh user per test for isolation.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Load env from cockpit so DATABASE_URL etc are available.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Use a per-test upload root so we don't pollute /tmp across runs.
os.environ.setdefault(
    "FIESTA_EARNINGS_UPLOAD_DIR",
    str(Path(_REPO_ROOT) / "tests" / "earnings" / "_tmp_uploads"),
)

sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def app():
    """Flask app in TESTING mode, with all blueprints registered."""
    import main  # noqa: F401  (registers everything)
    from app import app as flask_app, db
    import fiesta.earnings.models  # noqa: F401

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


def _make_user(db_session, suffix: str):
    """Per-test unique email so a previous run's leftover row doesn't collide.

    The remittance suite leaves rows on test failure too; we add a uuid hex
    so each test gets a fresh email regardless of prior state.
    """
    from models import User
    from werkzeug.security import generate_password_hash

    unique = uuid.uuid4().hex[:8]
    email = f"pytest_earnings_{suffix}_{unique}@fiesta.local"
    u = User(
        email=email,
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Earnings Test {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def user(db_session):
    from models import AuditLog, User
    from fiesta.earnings.models import Statement, IncomeEntry
    u = _make_user(db_session, "u1")
    yield u
    # Cascade cleanup: statements (and their entries) + manual entries + audit rows.
    # audit_log.user_id has a FK constraint to user, so we must clear those first.
    try:
        IncomeEntry.query.filter(IncomeEntry.user_id == u.id).delete()
        Statement.query.filter(Statement.user_id == u.id).delete()
        AuditLog.query.filter(AuditLog.user_id == u.id).delete()
        User.query.filter(User.id == u.id).delete()
        db_session.commit()
    except Exception:
        db_session.rollback()


def login_as(client, user):
    """Stamp the Flask-Login session cookie directly."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
