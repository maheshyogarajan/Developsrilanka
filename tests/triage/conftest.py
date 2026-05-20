"""Test fixtures for the S1 triage blueprint (Wave 1).

Mirrors tests/profile/conftest.py — load fiesta.env, import main so all blueprints
register, yield a flask test client + per-test User rows that get cleaned up in
teardown.
"""

import os
import sys
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


@pytest.fixture(scope="session")
def app():
    """Flask app with the S1 triage blueprint registered. main.py wires it in
    at import time; we also call register_routes again here so the fixture is
    safe to use even if a future refactor decouples them (idempotent)."""
    import main  # noqa: F401  — registers all blueprints including S1
    from app import app as flask_app, db
    from fiesta.triage import register_routes as register_triage_routes

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    # Idempotent — won't double-register.
    register_triage_routes(flask_app)

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


def _make_user(db_session, email_suffix: str):
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash
    u = User(
        email=f"pytest_s1_{email_suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest S1 {email_suffix}",
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
def user_a(db_session):
    """Per-test User row, deleted on teardown."""
    from models import User
    import uuid
    u = _make_user(db_session, uuid.uuid4().hex[:8] + "_a")
    yield u
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def user_b(db_session):
    """Second per-test User row, deleted on teardown."""
    from models import User
    import uuid
    u = _make_user(db_session, uuid.uuid4().hex[:8] + "_b")
    yield u
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


def login_as(client, user):
    """Set the Flask-Login session cookie directly to bypass the email/pw form."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
