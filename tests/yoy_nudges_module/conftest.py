"""
Shared fixtures for yoy_nudges tests.

Reuses tests/remittance/conftest.py for the app + db_session + _make_user
helpers so we get the same connect-to-Neon-and-clean-up behaviour the rest
of the suite uses.

Adds:
  * autouse fixture that ensures the YoYNudge model + table exist.
  * user_y fixture that creates a throwaway user and purges all yoy_nudge
    rows it owns on teardown.
  * subscription_factory_y — minimal Subscription inserter.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    _make_user,
)


@pytest.fixture(scope="session")
def app(_base_app):
    """Yield the base app — ensure YoY model + paywall model are loaded so
    the schedulers can query Subscription and write yoy_nudge."""
    from app import db
    from yoy_models import register_models as register_yoy
    register_yoy()
    try:
        from fiesta.paywall import register_models as register_paywall
        register_paywall()
    except Exception:
        pass
    with _base_app.app_context():
        db.create_all()
    return _base_app


@pytest.fixture(autouse=True)
def _ensure_models(app):
    """Make sure both models are registered before every test."""
    from yoy_models import register_models as register_yoy
    register_yoy()
    try:
        from fiesta.paywall import register_models as register_paywall
        register_paywall()
    except Exception:
        pass
    yield


def _purge_yoy_rows_for_user(user_id: int):
    """Best-effort cleanup of YoYNudge rows for one user."""
    try:
        from yoy_models import get_model
        YoYNudge = get_model()
        from app import db
        if YoYNudge is not None:
            YoYNudge.query.filter(YoYNudge.user_id == user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _purge_subscription_rows_for_user(user_id: int):
    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        from app import db
        if Subscription is not None:
            Subscription.query.filter(Subscription.user_id == user_id).delete(
                synchronize_session=False,
            )
            db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


@pytest.fixture
def user_y(db_session):
    """User scoped to this test, with full paywall + yoy + audit cleanup."""
    import uuid
    suffix = f"yoy_{uuid.uuid4().hex[:8]}"
    u = _make_user(db_session, suffix)
    yield u
    _purge_yoy_rows_for_user(u.id)
    _purge_subscription_rows_for_user(u.id)
    try:
        from models import User, AuditLog
        from app import db
        AuditLog.query.filter(AuditLog.user_id == u.id).delete()
        User.query.filter(User.id == u.id).delete()
        db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


@pytest.fixture
def subscription_factory_y(app, db_session):
    """Minimal Subscription inserter for YoY tests."""
    created = []

    def _make(user, *, tax_year=None, status="active",
              days_until_expiry=180, tier=None):
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        from fiesta.paywall.models import (
            current_sl_tax_year, expires_at_for_tax_year,
            TIER_SELF_FILE,
        )
        from app import db
        tax_year = tax_year or current_sl_tax_year()
        tier = tier or TIER_SELF_FILE
        expires_at = datetime.utcnow() + timedelta(days=days_until_expiry)
        sub = Subscription(
            user_id=user.id,
            tier=tier,
            tax_year=tax_year,
            purchased_at=datetime.utcnow(),
            expires_at=expires_at,
            status=status,
            amount_paid_lkr=2500,
        )
        db.session.add(sub)
        db.session.commit()
        created.append(sub.id)
        return sub

    yield _make

    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        from app import db
        if created and Subscription is not None:
            Subscription.query.filter(Subscription.id.in_(created)).delete(
                synchronize_session=False,
            )
            db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
