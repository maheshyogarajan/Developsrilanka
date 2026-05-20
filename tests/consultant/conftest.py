"""
Fixtures for X4 consultant booking tests (Wave 6, 2026-05-21).

Reuses the validated app + client + db_session + user_a/user_b fixtures from
``tests/remittance/conftest.py``. Adds:

  * ``_consultant_blueprint_registered`` autouse — guarantees the
    /consultant/book blueprint + Booking model are available.
  * ``booking_factory`` — direct-insert a Booking row bypassing Stripe.
  * Per-test purge of consultant_booking rows for the test user.
"""
from __future__ import annotations

import pytest

# Re-export the base fixtures.
from tests.remittance.conftest import (  # noqa: F401
    app as _base_app, client, db_session, user_a, user_b, login_as, _make_user,
)


def _purge_bookings_for_user(user_id):
    try:
        from fiesta.consultant.models import Booking, register_models
        register_models()
        if Booking is None:
            return
        from app import db
        Booking.query.filter(Booking.user_id == user_id).delete(
            synchronize_session=False
        )
        db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


@pytest.fixture(scope="session")
def app(_base_app):
    """Wrap the base app — register the consultant blueprint + models."""
    from fiesta.consultant import register_routes as register_consultant
    if "consultant" not in _base_app.blueprints:
        register_consultant(_base_app)
    return _base_app


@pytest.fixture(autouse=True)
def _consultant_models_registered(app):
    """Make sure the consultant_booking table exists for every test."""
    from fiesta.consultant import register_models
    register_models()
    from app import db
    with app.app_context():
        db.create_all()
    yield


@pytest.fixture
def booking_factory(app, db_session, user_a):
    """Factory for inserting a Booking row directly (bypasses Stripe).

    Yields a callable: ``booking_factory(**overrides) -> Booking``.
    Cleans up all rows it created on teardown.
    """
    created_ids = []

    def _make(*, user=None, status="paid_awaiting_redirect",
              amount_paid_lkr=5000,
              stripe_payment_intent_id="pi_pytest_booking_001",
              stripe_session_id="cs_pytest_booking_001",
              prep_brief_sent_at=None):
        from fiesta.consultant.models import Booking
        from app import db
        u = user or user_a
        b = Booking(
            user_id=u.id,
            stripe_payment_intent_id=stripe_payment_intent_id,
            stripe_session_id=stripe_session_id,
            amount_paid_lkr=amount_paid_lkr,
            status=status,
            prep_brief_sent_at=prep_brief_sent_at,
            calendar_redirect_url="https://calendar.app.google/upp97vgtE7oYVdzn9",
        )
        db.session.add(b)
        db.session.commit()
        created_ids.append(b.id)
        return b

    yield _make

    # Teardown.
    from fiesta.consultant.models import Booking
    from app import db
    if created_ids:
        Booking.query.filter(Booking.id.in_(created_ids)).delete(
            synchronize_session=False
        )
        db.session.commit()


@pytest.fixture(autouse=True)
def _purge_user_bookings_after_test(db_session):
    """Belt-and-braces: after every test, purge consultant_booking rows for
    any pytest_user_*@fiesta.local emails the suite touched."""
    yield
    try:
        from fiesta.consultant.models import Booking, register_models
        from models import User
        register_models()
        if Booking is None:
            return
        from app import db
        ids = [u.id for u in User.query.filter(
            User.email.like("pytest_user_%@fiesta.local")
        ).all()]
        if ids:
            Booking.query.filter(Booking.user_id.in_(ids)).delete(
                synchronize_session=False
            )
            db.session.commit()
    except Exception:
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
