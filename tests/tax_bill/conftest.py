"""tests/tax_bill/conftest.py — fixtures for the tax-bill test subpackage.

The pre-existing tests in this directory (test_s12.py, test_audit_*) are
pure unit tests that don't need any Flask context. The new
test_no_paywall_on_view.py is an integration test that needs an
authenticated client + a User row.

Strategy: do NOT export autouse fixtures here (so the unit tests in this
directory stay light and DB-free). Export only the on-demand fixtures
that the integration test imports explicitly.

Why re-export from paywall conftest (and not directly from remittance):
the paywall conftest provides a user_a that purges paywall rows BEFORE
the User row is deleted. The /tax-bill GET path no longer fires the
paywall gate (per launch decision 2026-05-26), so PaywallEvent rows are
not created — but if a regression ever puts the gate back, the teardown
won't trip on FK violations.
"""
from __future__ import annotations

# Re-export the paywall conftest's session-scoped app + per-test fixtures.
# Tests that need them must request them by name in their function signature;
# autouse fixtures are deliberately NOT re-exported (see module docstring).
#
# We also re-export `_base_app` (under the same name) because the paywall
# `app` fixture depends on it positionally. Importing `app` without its
# upstream `_base_app` dependency would raise "fixture not found".
from tests.paywall.conftest import (  # noqa: F401
    _base_app,
    app,
    client,
    db_session,
    user_a,
    user_b,
    login_as,
    subscription_factory,
)
