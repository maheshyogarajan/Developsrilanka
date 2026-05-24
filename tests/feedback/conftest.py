"""
Test fixtures for the feedback widget endpoint (Sprint 4 Tier D4).

Mirrors tests/analytics/conftest.py — same prod-env load + app fixture,
plus a cleanup fixture that removes test-created `feedback` rows on
teardown so prod data stays clean.
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
    """Bootstraps the full Flask app so feedback_routes.register_routes
    runs and `feedback_models.Feedback` is bound to the live DB."""
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
def cleanup_feedback(app):
    """Track feedback rows created by a single test and delete them on
    teardown. Beacon writes flow through the live `feedback` table; we
    don't want test rows polluting production submissions."""
    from app import db
    from feedback_models import Feedback

    with app.app_context():
        before_ids = {r.id for r in Feedback.query.with_entities(Feedback.id).all()}
    yield
    with app.app_context():
        new_ids = [
            r.id for r in Feedback.query.with_entities(Feedback.id).all()
            if r.id not in before_ids
        ]
        if new_ids:
            Feedback.query.filter(Feedback.id.in_(new_ids)).delete(
                synchronize_session=False
            )
            db.session.commit()
