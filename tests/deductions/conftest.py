"""tests/deductions/conftest.py — Flask client fixture for S5 surface tests.

F4.3 + F4.4 (P2 polish, 2026-05-27) regression tests need an HTTP client
to GET /reduce-tax/ and verify CTAs resolve to real routes at render
time. The existing tests in this dir (test_s5.py) are headless calc
tests and don't need a client, so the fixture wasn't here before.

Mirrors the pattern from tests/platform/conftest.py + tests/remittance/
conftest.py — load fiesta.env, register all blueprints by importing
main, run in TESTING mode with CSRF disabled.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = Path("G:/My Drive/CEO OS/working files/_cockpit_fiesta/fiesta.env")
if _ENV_PATH.exists():
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session")
def app():
    """Flask app in TESTING mode. Importing `main` registers every
    blueprint including fiesta_service_providers + fiesta_property so
    `url_for(...)` in the deductions template resolves to real routes."""
    import main  # noqa: F401
    from app import app as flask_app, db
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        db.create_all()
    yield flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c
