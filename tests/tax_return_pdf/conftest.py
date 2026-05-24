"""Tier D2-bpdf test fixtures.

Re-uses the paywall conftest scaffolding (Flask app + paywall blueprint +
subscription factory) but mints a UNIQUE-PER-TEST user email so leftover
rows from any previously-failed test run don't cause UniqueViolation on
re-run. Pattern: pytest_d2bpdf_<rand>@fiesta.local.

This module is the single source of truth for tier-d2-bpdf test wiring.
The IRD return PDF route is gated at min_tier=self_file (same as the rest
of /tax-bill), so we need the paywall fixtures too.
"""
from __future__ import annotations

import secrets

import pytest

# Re-export the base remittance fixtures (the paywall conftest needs
# `_base_app` to be in scope as an importable alias).
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    login_as,
    _make_user,
)

# Re-export the paywall app + helpers (model registration, subscription
# factory). We intentionally DO NOT re-export the paywall user_a/user_b
# fixtures — we redefine user_a below with unique-per-test email so a
# crashed prior run doesn't block re-runs.
from tests.paywall.conftest import (  # noqa: F401
    app,
    subscription_factory,
    _paywall_models_registered,
    _purge_paywall_tombstones,
    _purge_paywall_rows_for_user,
)


@pytest.fixture
def user_a(db_session):
    """Per-test user with a unique email so leftover rows from a previously
    crashed run don't block setup. Same paywall-aware teardown as the
    paywall conftest's user_a fixture."""
    from models import User
    suffix = f"d2bpdf_{secrets.token_hex(4)}"
    u = _make_user(db_session, suffix, persona="sl_foreign_income")
    yield u
    # Paywall cleanup FIRST (Subscription FK references PaywallEvent which
    # references User; order matters).
    _purge_paywall_rows_for_user(u.id)
    from remittance_models import RemittanceEntry, RemittanceImportBatch
    from models import AuditLog
    AuditLog.query.filter(AuditLog.user_id == u.id).delete()
    RemittanceEntry.query.filter(RemittanceEntry.user_id == u.id).delete()
    RemittanceImportBatch.query.filter(
        RemittanceImportBatch.user_id == u.id
    ).delete()
    User.query.filter(User.id == u.id).delete()
    db_session.commit()
