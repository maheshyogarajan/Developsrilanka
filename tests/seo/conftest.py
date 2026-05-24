"""
Test fixtures for the SEO + Article engine (Tier D6 A4 slice 1, 2026-05-24).

Mirrors tests/faq/conftest.py — same prod-env load + Flask app + client
fixtures. The SEO routes read from disk (content/articles/*.md), not from
the DB, so no row-cleanup fixture is needed; tests assert against the
shipped pilot articles plus whatever the loader picks up at test time.
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
    """Boots the full Flask app so seo_routes.register_routes runs and
    the article cache is populated from content/articles/*.md."""
    import main  # noqa: F401 — triggers full blueprint registration
    from app import app as flask_app, db
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    with flask_app.app_context():
        db.create_all()
    # Reload the article cache so tests pick up any fixture files added
    # since the lru_cache was first populated by an earlier test session.
    from seo_routes import _reload_articles
    _reload_articles()
    yield flask_app


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c
