"""Re-export the shared `app` + `client` fixtures so tests in this module
discover them. Matches the pattern used by tests/auth/conftest.py."""
from __future__ import annotations

from tests.remittance.conftest import (  # noqa: F401
    app,
    client,
    db_session,
)
