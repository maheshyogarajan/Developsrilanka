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
# We DO NOT re-export user_a/user_b — we define our own below so we can purge
# PaywallEvent / Subscription rows BEFORE the User row is deleted (the
# remittance fixture's user teardown otherwise hits FK violations).
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    login_as,
    _make_user,
)


def _purge_paywall_rows_for_user(user_id):
    """Best-effort purge of all paywall rows for a single user, run BEFORE
    user deletion to avoid FK violations.

    Order matters: Subscription references PaywallEvent (triggering_paywall_event_id),
    so Subscriptions must be deleted FIRST, then PaywallEvents.
    """
    try:
        from fiesta.paywall import get_models
        Subscription, PaywallEvent, _ = get_models()
        from app import db
        if Subscription is not None:
            Subscription.query.filter(Subscription.user_id == user_id).delete(
                synchronize_session=False
            )
            db.session.flush()
        if PaywallEvent is not None:
            PaywallEvent.query.filter(PaywallEvent.user_id == user_id).delete(
                synchronize_session=False
            )
        db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


@pytest.fixture
def user_a(db_session):
    """Drop-in for tests.remittance.conftest.user_a — but purges paywall rows
    BEFORE deleting the User row."""
    from models import User
    u = _make_user(db_session, "user_a", persona="sl_foreign_income")
    yield u
    # Paywall cleanup FIRST (before FKs trip)
    _purge_paywall_rows_for_user(u.id)
    # Then the standard remittance + user cleanup
    from remittance_models import RemittanceEntry, RemittanceImportBatch
    from models import AuditLog
    AuditLog.query.filter(AuditLog.user_id == u.id).delete()
    RemittanceEntry.query.filter(RemittanceEntry.user_id == u.id).delete()
    RemittanceImportBatch.query.filter(RemittanceImportBatch.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture
def user_b(db_session):
    """Drop-in for tests.remittance.conftest.user_b — paywall-aware teardown."""
    from models import User
    u = _make_user(db_session, "user_b", persona="sl_foreign_income")
    yield u
    _purge_paywall_rows_for_user(u.id)
    from remittance_models import RemittanceEntry, RemittanceImportBatch
    from models import AuditLog
    AuditLog.query.filter(AuditLog.user_id == u.id).delete()
    RemittanceEntry.query.filter(RemittanceEntry.user_id == u.id).delete()
    RemittanceImportBatch.query.filter(RemittanceImportBatch.user_id == u.id).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()


@pytest.fixture(scope="session")
def app(_base_app):
    """Wrap the base app — register the paywall blueprint defensively, AND
    add the test-only gated view BEFORE Flask handles its first request
    (Flask blocks late route registration via _check_setup_finished)."""
    from fiesta.paywall import register_routes as register_paywall
    if "paywall" not in _base_app.blueprints:
        register_paywall(_base_app)

    # Register the test-only gated view eagerly so it's present before
    # any test triggers the first request.
    test_path = "/_test/paywall/S6"
    if not any(rule.rule == test_path for rule in _base_app.url_map.iter_rules()):
        from fiesta.paywall import paywall_required, TIER_SELF_FILE
        from flask import jsonify

        @_base_app.route(test_path, methods=["GET"], endpoint="_test_paywall_S6")
        @paywall_required(min_tier=TIER_SELF_FILE, screen_id="S6",
                          action="test_view")
        def gated_test_view():
            return jsonify({"ok": True})

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


@pytest.fixture(scope="session")
def gated_view_path(app):
    """Register a one-off /_test/paywall/S6 view gated at self_file. SESSION-scoped
    so the route is added BEFORE Flask handles any requests (post-first-request
    route registration is blocked by Flask).

    Returns the URL path. Idempotent.
    """
    from fiesta.paywall import paywall_required, TIER_SELF_FILE
    from flask import jsonify
    path = "/_test/paywall/S6"
    if not any(rule.rule == path for rule in app.url_map.iter_rules()):
        @app.route(path, methods=["GET"], endpoint="_test_paywall_S6")
        @paywall_required(min_tier=TIER_SELF_FILE, screen_id="S6",
                          action="test_view")
        def gated():
            return jsonify({"ok": True})
    return path


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
def _purge_paywall_tombstones(db_session, request):
    """After every test, sweep evt_pytest_* StripeEvent tombstone rows.

    PaywallEvent and Subscription cleanup happens INSIDE user_a / user_b
    fixture teardowns above (before User.delete, so FK constraints don't trip).
    """
    yield
    try:
        from fiesta.paywall import get_models
        _, _, StripeEvent = get_models()
        from app import db
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
