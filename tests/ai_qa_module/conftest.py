"""Test fixtures for ai_qa + /api/qa.

Mirrors tests/feedback/conftest.py — loads the prod env file so we can
bootstrap the full Flask app (qa_routes.register_routes runs on import
of main).

For the pure ai_qa retrieval test we don't need the Flask app, so the
`app` fixture is scoped session and skipped when not required.
"""
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
    """Boot the Flask app once so /api/qa is registered."""
    import main  # noqa: F401 — triggers full blueprint registration

    from app import app as flask_app  # type: ignore
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture()
def client(app):
    return app.test_client()
