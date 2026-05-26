"""Test fixtures for the income-source /new route variants (C6 Day-0 fix).

Reuses the validated app/client/db_session/login_as helpers from
tests/profile/conftest.py — we only need a logged-in user + a working
Flask app with the rsu / crypto / professional_fees blueprints registered.
The existing main.py + profile conftest already does this.
"""
from __future__ import annotations

# Re-export fixtures from the profile conftest. The profile conftest already
# imports main (which registers rsu / crypto / professional_fees) and gives
# us a session-scoped Flask app + per-test users with onboarding_completed=True.
from tests.profile.conftest import (  # noqa: F401
    app,
    client,
    db_session,
    user_a,
    user_b,
    login_as,
    _make_user,
)
