"""Test fixtures for the X8a public-flow surface.

Mirrors tests/triage/conftest.py — load fiesta.env, import main so all blueprints
register, yield a Flask test client + per-test User rows that get cleaned up in
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
    """Flask app with every blueprint registered (main.py does the wiring)."""
    import main  # noqa: F401 — registers all blueprints

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


@pytest.fixture
def user_a(db_session):
    """Per-test verified, onboarded User. Deleted on teardown."""
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash
    import uuid

    suffix = uuid.uuid4().hex[:8]
    u = User(
        email=f"pytest_x8a_{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest X8a {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    yield u
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


def login_as(client, user):
    """Set the Flask-Login session cookie directly."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
