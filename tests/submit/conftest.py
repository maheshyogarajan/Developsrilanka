"""
tests/submit/conftest.py — fixtures for the submit subpackage.

Re-exports the validated app + client + db_session + user fixtures from
tests/remittance/conftest.py so the Auto-File recovery tests (and any
future S14 integration tests) can use them.

Pure unit tests in test_s14.py don't need these — they predate the live-DB
fixture pattern.
"""
from __future__ import annotations

# Re-export the base remittance fixtures.
from tests.remittance.conftest import (  # noqa: F401
    app, client, db_session, user_a, user_b, login_as, _make_user,
)
