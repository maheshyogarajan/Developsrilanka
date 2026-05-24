"""
Test fixtures for the client-side analytics beacon (Sprint 4 Tier B).

Mirrors tests/remittance/conftest.py — same prod-env load + app fixture
(TESTING + CSRF disabled at the Flask-WTF layer so we can test the
endpoint's Origin/Referer gate independently). The /api/event route uses
@csrf.exempt regardless, but disabling CSRF globally keeps test ergonomics
consistent with the rest of the suite.
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
    """Same `main`-bootstrapped app the remittance tests use, so the beacon
    blueprint registration runs and the EVENT SPINE migration applies."""
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
def db_session(app):
    from app import db as _db
    with app.app_context():
        yield _db.session
        _db.session.rollback()


@pytest.fixture
def cleanup_events(app):
    """Track event rows created by a single test and delete them on teardown.

    Beacon writes flow through the live `events` table; we don't want test
    rows polluting the production analytics."""
    from app import db
    from event_models import Event

    with app.app_context():
        before_ids = {r.id for r in Event.query.with_entities(Event.id).all()}
    yield
    with app.app_context():
        new_ids = [
            r.id for r in Event.query.with_entities(Event.id).all()
            if r.id not in before_ids
        ]
        if new_ids:
            Event.query.filter(Event.id.in_(new_ids)).delete(synchronize_session=False)
            db.session.commit()
