"""Pytest fixtures for the F5 GDPR + SL PDPA compliance baseline.

Mirrors tests/fiesta_public/conftest.py: load fiesta.env so app boots, import
main so every blueprint registers (including data_rights), expose a Flask
test client + per-test User fixture.
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
    """Flask app with every blueprint registered."""
    import main  # noqa: F401 -- side-effect import; registers blueprints

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
    """Per-test verified, onboarded user. Removed in teardown."""
    from datetime import datetime, timedelta
    import uuid
    from models import User
    from werkzeug.security import generate_password_hash

    suffix = uuid.uuid4().hex[:8]
    u = User(
        email=f"pytest_f5_{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest F5 {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    user_id = u.id
    yield u
    # Hard-delete the test row even if the test soft-deleted it.
    User.query.filter(User.id == user_id).delete()
    db_session.commit()


def login_as(client, user):
    """Set the Flask-Login session cookie directly (skips CSRF on form login)."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
