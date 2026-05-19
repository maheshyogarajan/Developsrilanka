"""Test fixtures for the S3 profile blueprint (Wave 3).

Pattern mirrors tests/remittance/conftest.py — load fiesta.env, import main so
all blueprints register, register the new fiesta_profile blueprint, run db.create_all,
yield a flask test client + per-test User rows that get cleaned up in teardown.
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
    """Flask app with the S3 profile blueprint registered. main.py handles legacy
    blueprint registrations; we also explicitly register fiesta_profile because
    main.py hasn't been edited yet to call it (we'll wire that in a final commit)."""
    import main  # noqa: F401  — registers legacy blueprints
    from app import app as flask_app, db
    from fiesta.profile.routes import register_blueprint as register_fiesta_profile

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False

    # Idempotent — won't double-register if main.py also called this.
    register_fiesta_profile(flask_app)

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
        email=f"pytest_s3_{email_suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest S3 {email_suffix}",
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
    from models import User
    from fiesta.profile.models import FiestaProfile
    u = _make_user(db_session, "user_a")
    yield u
    FiestaProfile.query.filter(FiestaProfile.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def user_b(db_session):
    from models import User
    from fiesta.profile.models import FiestaProfile
    u = _make_user(db_session, "user_b")
    yield u
    FiestaProfile.query.filter(FiestaProfile.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


def login_as(client, user):
    """Set the Flask-Login session cookie directly to bypass the email/pw form."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
