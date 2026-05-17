"""
Test fixtures for remittance hardening tests (Wave H, council #1).

Loads env from working files/_cockpit_fiesta/fiesta.env so DATABASE_URL / GEMINI_API_KEY
are available. Provides a Flask test client + a teardown that rolls back any
RemittanceEntry / RemittanceImportBatch / User rows created within the test.
"""
import os
import sys
from pathlib import Path

import pytest

# Load environment from the prod env file. The tests run against the same
# Neon DB the live app uses — they create user_email='pytest_…@fiesta.local'
# rows and clean them up in teardown, so this is safe and realistic.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

# Ensure the repo root is on sys.path so `import app` / `import models` works
# when pytest is invoked from the repo root.
sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def app():
    """The Flask app, in TESTING mode. Ensures the new Wave H tables exist."""
    from app import app as flask_app, db
    # Importing remittance_models registers RemittanceImportBatch with SQLAlchemy
    # metadata so the create_all below picks it up.
    import remittance_models  # noqa: F401
    flask_app.config["TESTING"] = True
    with flask_app.app_context():
        db.create_all()
    yield flask_app


@pytest.fixture
def client(app):
    """Vanilla unauthenticated test client."""
    with app.test_client() as c:
        yield c


@pytest.fixture
def db_session(app):
    """A db.session bound to the test app context, with explicit cleanup."""
    from app import db as _db
    with app.app_context():
        yield _db.session
        _db.session.rollback()


def _make_user(db_session, email_suffix: str, persona=None):
    """Create a User row for the test and return it. Caller is responsible for
    deleting it in teardown if needed.

    Note: production DB has access_expiration_date NOT NULL (schema drift from
    the model's nullable=True). We set a far-future date for the test row.
    """
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash
    u = User(
        email=f"pytest_{email_suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest {email_suffix}",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
        persona=persona,
    )
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def user_a(db_session):
    from models import User
    u = _make_user(db_session, "user_a", persona="sl_foreign_income")
    yield u
    # Cleanup: delete any audit log, entries, batches, then the user
    from remittance_models import RemittanceEntry, RemittanceImportBatch
    from models import AuditLog
    AuditLog.query.filter(AuditLog.user_id == u.id).delete()
    RemittanceEntry.query.filter(RemittanceEntry.user_id == u.id).delete()
    RemittanceImportBatch.query.filter(RemittanceImportBatch.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def user_b(db_session):
    from models import User
    u = _make_user(db_session, "user_b", persona="sl_foreign_income")
    yield u
    from remittance_models import RemittanceEntry, RemittanceImportBatch
    from models import AuditLog
    AuditLog.query.filter(AuditLog.user_id == u.id).delete()
    RemittanceEntry.query.filter(RemittanceEntry.user_id == u.id).delete()
    RemittanceImportBatch.query.filter(RemittanceImportBatch.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


def login_as(client, user):
    """Bypass the email/password form by setting the Flask-Login session cookie
    directly. Works because the test client + the app share the same secret key."""
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
