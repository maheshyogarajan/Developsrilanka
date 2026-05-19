"""Test fixtures for X2 persona tests. Mirrors tests/remittance/conftest.py.

Creates the persona tables before tests run + provides isolated test users."""
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


@pytest.fixture(scope="session")
def app():
    """Flask app in TESTING mode. Imports main so X2 routes register."""
    import main  # noqa: F401
    from app import app as flask_app, db
    from fiesta.persona.models import Persona, PersonaInterest  # noqa: F401
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
    from models import User
    from werkzeug.security import generate_password_hash
    u = User(
        email=f"pytest_x2_{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest X2 {suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _cleanup_user(db_session, u):
    from fiesta.persona.models import Persona, PersonaInterest
    from models import User
    PersonaInterest.query.filter_by(user_id=u.id).delete()
    Persona.query.filter_by(user_id=u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def user_x(db_session):
    u = _make_user(db_session, "x")
    yield u
    _cleanup_user(db_session, u)


@pytest.fixture
def user_y(db_session):
    u = _make_user(db_session, "y")
    yield u
    _cleanup_user(db_session, u)


@pytest.fixture
def user_z(db_session):
    u = _make_user(db_session, "z")
    yield u
    _cleanup_user(db_session, u)


def login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
