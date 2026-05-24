"""Shared fixtures for tests/stripe_subscription/.

Reuses tests/paywall/conftest.py — same app, same Subscription model, same
user_a/user_b fixtures. We just need the subscription_bp registered, which
main.py does at import time (imported by the base app fixture).
"""
from __future__ import annotations

import pytest

# Re-export the paywall fixtures wholesale. tests/paywall/conftest already
# does the paywall_event / Subscription cleanup we need. _base_app is
# referenced internally by paywall.conftest.app, so it must be re-exported
# too even though no test uses it directly.
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
