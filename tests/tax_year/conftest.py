"""Test fixtures for tests/tax_year/.

Mirrors tests/platform/conftest.py so the cross-product YA unification tests
share the same app + client + user_factory wiring as the platform regression
suite. This keeps the canonical fixture in one place; the tax_year tests
just re-export the relevant pieces.
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

TAX_YEAR_TEST_PREFIX = "pytest_tax_year_"


@pytest.fixture(scope="session")
def app():
    import main  # noqa: F401  — registers every blueprint
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


def _make_user(db_session, suffix: str, *, role: str = "user"):
    from models import User
    from werkzeug.security import generate_password_hash

    u = User(
        email=f"{TAX_YEAR_TEST_PREFIX}{suffix}@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name=f"Pytest TaxYear {suffix}",
        role=role,
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
        income_sources=[],
    )
    db_session.add(u)
    db_session.commit()
    return u


def _cleanup_user(db_session, u):
    from models import AuditLog, User
    AuditLog.query.filter(AuditLog.user_id == u.id).delete(synchronize_session=False)
    User.query.filter(User.id == u.id).delete(synchronize_session=False)
    db_session.commit()


@pytest.fixture
def user_factory(db_session):
    created = []

    def _factory(suffix: str, *, role: str = "user"):
        u = _make_user(db_session, suffix, role=role)
        created.append(u)
        return u

    yield _factory

    for u in created:
        try:
            _cleanup_user(db_session, u)
        except Exception:
            db_session.rollback()


@pytest.fixture(autouse=True)
def _cleanup_orphans(db_session):
    yield
    from models import AuditLog, User
    orphans = User.query.filter(
        User.email.like(f"{TAX_YEAR_TEST_PREFIX}%")
    ).all()
    if orphans:
        ids = [u.id for u in orphans]
        AuditLog.query.filter(AuditLog.user_id.in_(ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(ids)).delete(synchronize_session=False)
        db_session.commit()


def login_as(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
