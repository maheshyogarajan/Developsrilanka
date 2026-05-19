"""
Shared fixtures for fiesta.paywall (X1) tests.

Reuses the validated app / client / db_session / user_a / user_b / login_as
helpers from tests/remittance/conftest.py. Adds:

  * autouse fixture that registers the paywall blueprint + models against
    the session-scoped Flask app (idempotent — main.py wiring is the
    orchestrator's job).
  * `subscription_factory` helper for tests that need to mint an active
    Subscription row without going through Stripe.
  * Per-test cleanup of paywall_event / paywall_subscription / paywall_stripe_event
    rows created by the tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

# Re-export the base remittance fixtures so paywall tests can use them.
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    user_a,
    user_b,
    login_as,
    _make_user,
)


@pytest.fixture(scope="session")
def app(_base_app):
    """Wrap the base app — register the paywall blueprint defensively."""
    from fiesta.paywall import register_routes as register_paywall
    if "paywall" not in _base_app.blueprints:
        register_paywall(_base_app)
    return _base_app


@pytest.fixture(autouse=True)
def _paywall_models_registered(app):
    """Ensure paywall models exist before any test runs."""
    from fiesta.paywall import register_models
    register_models()
    from app import db
    with app.app_context():
        db.create_all()
    yield


@pytest.fixture
def subscription_factory(app, db_session):
    """Factory for inserting a Subscription row directly (bypasses Stripe).

    Yields a callable: ``subscription_factory(user, **overrides) -> Subscription``.
    Cleans up all rows it created on teardown.
    """
    created_ids = []

    def _make(user, *, tier=None, tax_year=None, status="active",
              days_until_expiry=None, expires_at=None,
              stripe_payment_intent_id=None, amount_paid_lkr=None,
              triggering_paywall_event_id=None):
        from fiesta.paywall import get_models, TIER_SELF_FILE
        Subscription, _, _ = get_models()
        from fiesta.paywall.models import expires_at_for_tax_year, current_sl_tax_year
        from app import db

        tier = tier or TIER_SELF_FILE
        tax_year = tax_year or current_sl_tax_year()
        if expires_at is None:
            if days_until_expiry is not None:
                expires_at = datetime.utcnow() + timedelta(days=days_until_expiry)
            else:
                expires_at = expires_at_for_tax_year(tax_year)

        sub = Subscription(
            user_id=user.id,
            tier=tier,
            tax_year=tax_year,
            purchased_at=datetime.utcnow(),
            expires_at=expires_at,
            status=status,
            stripe_payment_intent_id=stripe_payment_intent_id,
            amount_paid_lkr=amount_paid_lkr,
            triggering_paywall_event_id=triggering_paywall_event_id,
        )
        db.session.add(sub)
        db.session.commit()
        created_ids.append(sub.id)
        return sub

    yield _make

    # Cleanup all rows we created.
    try:
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()
        from app import db
        if created_ids and Subscription is not None:
            Subscription.query.filter(Subscription.id.in_(created_ids)).delete(
                synchronize_session=False
            )
            db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _purge_paywall_rows(db_session, request):
    """After every test, sweep paywall rows tied to pytest_* users."""
    yield
    try:
        from fiesta.paywall import get_models
        Subscription, PaywallEvent, StripeEvent = get_models()
        from app import db
        from models import User
        # Find pytest user ids
        pytest_user_ids = [
            u.id for u in User.query.filter(User.email.like("pytest_%@fiesta.local")).all()
        ]
        if pytest_user_ids:
            if PaywallEvent is not None:
                PaywallEvent.query.filter(PaywallEvent.user_id.in_(pytest_user_ids)).delete(
                    synchronize_session=False
                )
            if Subscription is not None:
                Subscription.query.filter(Subscription.user_id.in_(pytest_user_ids)).delete(
                    synchronize_session=False
                )
        # Stripe-event tombstones inserted by tests use the prefix
        # "evt_pytest_". Sweep those.
        if StripeEvent is not None:
            StripeEvent.query.filter(
                StripeEvent.stripe_event_id.like("evt_pytest_%")
            ).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
