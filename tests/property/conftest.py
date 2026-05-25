"""Re-export base app/client + paywall-aware user fixtures.

Property routes are gated by both @login_required and
@paywall_required(min_tier='self_file', ...). To hit /property we need a
logged-in user with an active Self-File subscription — same setup as
tests/paywall/. We reuse that conftest's user_a + subscription_factory
to avoid duplicating the FK-aware teardown logic.

Fixture chain (matches tests/paywall/conftest.py):
  tests.remittance.conftest.app  ->  re-exported here as `_base_app`
  tests.paywall.conftest.app     ->  wraps _base_app to register the
                                       paywall blueprint
  here                            ->  re-exports the paywall-wrapped `app`
                                       plus client / db_session / user fixtures
"""
from __future__ import annotations

# The paywall conftest's `app` fixture has `def app(_base_app)` signature,
# so pytest must be able to resolve `_base_app` in the *current* fixture
# scope. We bring it in here under the same alias.
from tests.remittance.conftest import app as _base_app  # noqa: F401

# Then bring in the paywall-aware fixtures.
from tests.paywall.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    user_a,
    user_b,
    subscription_factory,
    _paywall_models_registered,
)
