"""
Test fixtures for the FAQ / Help routes (Tier D3, 2026-05-24).

Mirrors tests/feedback/conftest.py — same prod-env load + Flask app +
client fixtures, plus a cleanup fixture that removes FAQEntry rows
created by a single test so prod data stays clean.
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
    """Boots the full Flask app so faq_routes.register_routes runs and
    `faq_models.FAQEntry` is bound to the live DB."""
    import main  # noqa: F401 — triggers full blueprint registration
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


@pytest.fixture
def cleanup_faqs(app):
    """Delete FAQEntry rows created during the test, so re-runs stay
    deterministic and prod data isn't polluted."""
    from app import db
    from faq_models import FAQEntry
    with app.app_context():
        before = {r.id for r in FAQEntry.query.with_entities(FAQEntry.id).all()}
    yield
    with app.app_context():
        new = [
            r.id for r in FAQEntry.query.with_entities(FAQEntry.id).all()
            if r.id not in before
        ]
        if new:
            FAQEntry.query.filter(FAQEntry.id.in_(new)).delete(
                synchronize_session=False
            )
            db.session.commit()
