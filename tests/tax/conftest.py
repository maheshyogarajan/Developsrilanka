"""tests/tax/conftest.py — fixtures for MS2 E.0 canonical-models tests.

Self-contained: spins up an in-memory SQLite DB with the canonical schema +
related tables (user, remittance_entries). Does NOT load the full Flask app
or any prod-DB blueprints — that would pull in Stripe, Sentry, S3, etc.
and slow the test suite to a crawl.

Pattern: set DATABASE_URL=sqlite:///:memory: BEFORE any model imports,
then import app+db, then import the model modules so the metadata gets
registered, then db.create_all() against the in-memory DB.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# CRITICAL: must run before any `from app import db` happens.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# Quieten the verbose perf/Sentry init in tests.
os.environ.setdefault("SENTRY_DSN", "")
# Cross-tax-engine tests need this too (see top-level conftest).
os.environ.setdefault("EVENTS_SYNC_FOR_TEST", "1")

# Repo root on sys.path so `import app` / `import models` resolve when pytest
# is invoked from any directory.
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def app_ctx():
    """Spin up Flask app + in-memory SQLite + all tax models registered."""
    from app import app as flask_app, db

    flask_app.config["TESTING"] = True
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    flask_app.config["WTF_CSRF_ENABLED"] = False

    # Register model metadata. Order matters: models.py defines User which
    # remittance_models.py and fiesta.tax.models both FK to. B12 business_income
    # ORM models must be imported before db.create_all so the FK from
    # incomes.business_income_id resolves to business_income_entries.id.
    import models  # noqa: F401
    import remittance_models  # noqa: F401
    import fiesta.tax.models  # noqa: F401
    import fiesta.tax.business_income  # noqa: F401

    with flask_app.app_context():
        # db.create_all picks up everything in metadata, including the new
        # canonical-models tables and the new User columns.
        db.create_all()
        yield flask_app
        db.session.rollback()
        db.drop_all()


@pytest.fixture
def session(app_ctx):
    """A db.session bound to the test app context. Rolls back on teardown."""
    from app import db
    with app_ctx.app_context():
        yield db.session
        db.session.rollback()


@pytest.fixture
def user(session):
    """A throw-away User row for tests that need a valid user_id FK target.

    SQLite-on-Flask-SQLAlchemy doesn't honour FK CASCADE by default (PRAGMA
    foreign_keys is OFF) so we explicitly purge child rows on teardown to
    keep test isolation. Without this purge, AssetDisposal / CryptoPosition
    rows written by test N leak into test N+1's queries via user_id=1
    (auto-increment reuses the ID after DELETE on plain INTEGER PRIMARY KEY).
    """
    from datetime import datetime, timedelta
    from models import User
    u = User(
        email="pytest_e0_canonical@fiesta.local",
        name="Pytest E0",
        role="user",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    session.add(u)
    session.commit()
    yield u
    # Purge per-user fiesta.tax rows BEFORE deleting the user (CASCADE FK is
    # off on SQLite). Catch + ignore for tests that don't load these models.
    try:
        from fiesta.tax.models import (
            AssetDisposal, CryptoPosition, Income, RSUVestingEvent,
        )
        for _cls in (Income, AssetDisposal, CryptoPosition, RSUVestingEvent):
            try:
                _cls.query.filter_by(user_id=u.id).delete(synchronize_session=False)
            except Exception:
                session.rollback()
        session.commit()
    except Exception:
        session.rollback()
    session.delete(u)
    session.commit()
