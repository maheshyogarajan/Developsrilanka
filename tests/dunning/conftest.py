"""Shared fixtures for tests/dunning/.

Mirrors tests/stripe_subscription/conftest.py — same app, same Subscription
model, same user fixtures. We just need the subscription_bp registered so the
webhook integration test can POST to /webhooks/stripe/subscription.

Following the same DB-mocking pattern as the existing stripe_subscription
tests: the dunning table migration (migrations/add_dunning.py) needs to be
applied to Neon before any DB-backed integration tests can pass; that's a
CEO deploy step. Tests stub the DB layer so they verify behaviour without
touching unmigrated tables.
"""
from __future__ import annotations

import pytest

from tests.paywall.conftest import (  # noqa: F401
    _base_app, app, client, db_session, user_a, user_b, login_as,
    subscription_factory, _paywall_models_registered,
    _purge_paywall_tombstones,
)


@pytest.fixture(autouse=True)
def _ensure_subscription_bp(app):
    """Make sure the subscription blueprint is registered on the test app.
    main.py wires it at import time; this is the belt-and-braces."""
    if "stripe_subscription" not in app.blueprints:
        from webhooks.stripe_subscription import register_routes
        register_routes(app)
    yield
