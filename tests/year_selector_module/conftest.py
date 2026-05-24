"""Tier D4 C4 year-selector test fixtures.

Mirrors tests/tax_return_pdf/conftest.py: re-exports the paywall + remittance
fixtures (Flask app, db_session, login helper) and mints a unique-per-test user
so leftover rows don't block re-runs.
"""
from __future__ import annotations

import secrets

import pytest

# Re-export the base remittance fixtures.
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app,
    client,
    db_session,
    login_as,
    _make_user,
)

# Re-export the paywall app + helpers (model registration, subscription
# factory). We redefine user_a below with unique-per-test email.
from tests.paywall.conftest import (  # noqa: F401
    app,
    subscription_factory,
    _paywall_models_registered,
    _purge_paywall_tombstones,
    _purge_paywall_rows_for_user,
)


@pytest.fixture
def user_a(db_session):
    """Per-test user with a unique email so leftover rows don't block setup."""
    from models import User
    suffix = f"d4c4_{secrets.token_hex(4)}"
    u = _make_user(db_session, suffix, persona="sl_foreign_income")
    yield u
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
