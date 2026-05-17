"""
Shared fixtures for AI-run / Wave 2 dashboard tests.

Re-uses the already-validated fixtures from tests/remittance/conftest.py
(app, client, db_session, user_a, user_b, login_as helper) — they handle env
loading, sys.path bootstrap, Flask app construction with all blueprints
registered, and per-test user create/teardown against the live Neon DB.

Adds an `admin_user` fixture for tests that need to exercise the admin gate
on /admin/revenue.
"""
import pytest

# Re-export everything from the remittance conftest. pytest discovers fixtures
# by name in scope; importing them at module level makes them available to any
# test in tests/ai_run/.
from tests.remittance.conftest import (  # noqa: F401
    client,
    db_session,
    user_a,
    user_b,
    login_as,
    _make_user,
)
# We override `app` below so we can register the revenue_intel blueprint
# defensively — main.py wiring is the orchestrator's job (per Wave 2.1 subagent
# contract: "DO NOT touch main.py"), and we still need the blueprint live for
# the route-level tests. The base `app` fixture is imported as `_base_app`.
from tests.remittance.conftest import app as _base_app  # noqa: F401


@pytest.fixture(scope="session")
def app(_base_app):
    """Wrap the base app fixture: ensure ai_run blueprints are registered.

    Re-registration is idempotent-safe: Flask raises if you register the same
    blueprint twice with the same name, so we check `_base_app.blueprints`
    before calling register_routes.
    """
    if "revenue_intel" not in _base_app.blueprints:
        from revenue_intel import register_routes as register_revenue_routes
        register_revenue_routes(_base_app)
    if "customer_brain" not in _base_app.blueprints:
        # Wave 2.3 AI CRM admin views — same defensive registration pattern.
        from customer_brain_routes import register_routes as register_customer_brain_routes
        register_customer_brain_routes(_base_app)
    return _base_app


@pytest.fixture
def admin_user(db_session):
    """A user with role='admin'. Used to verify the admin gate on /admin/revenue.

    Mirrors the user_a/user_b lifecycle (create, yield, delete) but flips the
    role so the @login_required + role check both pass.
    """
    from datetime import datetime, timedelta
    from models import User
    from werkzeug.security import generate_password_hash

    u = User(
        email="pytest_admin_ai_run@fiesta.local",
        password_hash=generate_password_hash("pytest-pw-not-real"),
        name="Pytest Admin AI Run",
        role="admin",
        subscription_status="free_trial",
        access_expiration_date=datetime.utcnow() + timedelta(days=365),
        is_email_verified=True,
        onboarding_completed=True,
    )
    db_session.add(u)
    db_session.commit()
    yield u

    # Teardown — also purge any dashboard_viewed events we emitted while testing.
    try:
        from event_models import Event
        Event.query.filter(Event.user_id == u.id).delete()
    except Exception:
        # Event model may not be importable in some weird state — non-fatal.
        pass
    User.query.filter(User.id == u.id).delete()
    db_session.commit()
