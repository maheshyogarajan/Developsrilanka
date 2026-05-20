"""
X4 Consultant booking — route + webhook + model coverage (Wave 6, 2026-05-21).
"""
from __future__ import annotations

import json

import pytest


# --------------------------------------------------------------------------- #
# Landing page
# --------------------------------------------------------------------------- #

def test_anon_request_to_book_landing_redirects_to_login(client):
    resp = client.get("/consultant/book", follow_redirects=False)
    assert resp.status_code in (301, 302)
    loc = (resp.headers.get("Location") or "").lower()
    # Either /login or somewhere obviously auth-related.
    assert "/login" in loc or "/auth" in loc, loc


def test_signed_in_user_sees_book_landing(client, user_a):
    from tests.remittance.conftest import login_as
    login_as(client, user_a)
    resp = client.get("/consultant/book")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Book a consultation" in html
    assert "Rs 5,000" in html
    assert "30" in html  # session minutes
    assert "/consultant/book/checkout" in html


# --------------------------------------------------------------------------- #
# Checkout flow (Stripe absent)
# --------------------------------------------------------------------------- #

def test_checkout_without_stripe_key_flashes_and_returns_to_landing(
        client, user_a, monkeypatch):
    """When STRIPE_SECRET_KEY is missing, the route warns + redirects back
    to /consultant/book (NOT to Stripe). The customer never sees a 5xx."""
    from tests.remittance.conftest import login_as
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    login_as(client, user_a)
    resp = client.post("/consultant/book/checkout",
                        data={"return_to": "/dashboard"},
                        follow_redirects=False)
    assert resp.status_code in (301, 302)
    loc = resp.headers.get("Location", "")
    assert "/consultant/book" in loc


def test_cancel_route_redirects_to_landing_with_flash(client, user_a):
    from tests.remittance.conftest import login_as
    login_as(client, user_a)
    resp = client.get("/consultant/book/cancel", follow_redirects=False)
    assert resp.status_code in (301, 302)
    assert "/consultant/book" in (resp.headers.get("Location") or "")


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

def test_booking_model_persists_idempotently_by_payment_intent(
        app, db_session, user_a):
    """Two inserts with the same stripe_payment_intent_id should violate the
    UNIQUE constraint — proves the column is actually unique-indexed."""
    from fiesta.consultant.models import Booking, register_models
    register_models()
    from app import db
    from datetime import datetime

    b1 = Booking(user_id=user_a.id,
                  stripe_payment_intent_id="pi_pytest_uniq_test_001",
                  amount_paid_lkr=5000,
                  status="paid_awaiting_redirect",
                  purchased_at=datetime.utcnow())
    db.session.add(b1)
    db.session.commit()

    with pytest.raises(Exception):
        b2 = Booking(user_id=user_a.id,
                      stripe_payment_intent_id="pi_pytest_uniq_test_001",
                      amount_paid_lkr=5000,
                      status="paid_awaiting_redirect",
                      purchased_at=datetime.utcnow())
        db.session.add(b2)
        db.session.commit()
    db.session.rollback()

    # Clean up.
    Booking.query.filter_by(
        stripe_payment_intent_id="pi_pytest_uniq_test_001"
    ).delete(synchronize_session=False)
    db.session.commit()


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #

def test_webhook_without_secret_returns_503(client):
    import os
    # Strip ALL three possible secret env vars so we hit the 503 path.
    for name in ("STRIPE_CONSULTANT_WEBHOOK_SECRET",
                 "STRIPE_PAYWALL_WEBHOOK_SECRET",
                 "STRIPE_WEBHOOK_SECRET"):
        os.environ.pop(name, None)
    resp = client.post("/webhooks/stripe/consultant",
                        data="{}",
                        headers={"Stripe-Signature": "irrelevant"})
    assert resp.status_code == 503


def test_handle_checkout_completed_creates_booking(app, user_a, monkeypatch):
    """Direct-call the webhook handler with a synthetic Stripe event.

    Bypasses signature verification by calling _handle_checkout_completed
    directly — that's the unit under test (signature verification is
    Stripe SDK territory).
    """
    from fiesta.consultant.routes import _handle_checkout_completed
    from fiesta.consultant.models import Booking, register_models
    register_models()
    from app import db

    fake_event = {
        "id": "evt_pytest_consultant_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_pytest_consultant_001",
                "payment_intent": "pi_pytest_consultant_001",
                "amount_total": 500000,  # cents (Rs 5,000 * 100)
                "metadata": {
                    "user_id": str(user_a.id),
                    "product": "consultant_booking",
                    "return_to": "/dashboard",
                },
            }
        },
    }
    with app.app_context():
        _handle_checkout_completed(fake_event)

        b = Booking.query.filter_by(
            stripe_payment_intent_id="pi_pytest_consultant_001"
        ).first()
        assert b is not None, "Booking row must be created by the webhook"
        assert b.user_id == user_a.id
        assert b.amount_paid_lkr == 5000
        assert b.status == "paid_awaiting_redirect"
        assert b.calendar_redirect_url.startswith("https://calendar.app.google/")

        # Idempotency — second call with same payment_intent_id is a no-op.
        _handle_checkout_completed(fake_event)
        all_rows = Booking.query.filter_by(
            stripe_payment_intent_id="pi_pytest_consultant_001"
        ).all()
        assert len(all_rows) == 1, (
            f"Expected idempotency — got {len(all_rows)} rows for the same "
            f"payment_intent"
        )

        # Teardown.
        Booking.query.filter_by(
            stripe_payment_intent_id="pi_pytest_consultant_001"
        ).delete(synchronize_session=False)
        db.session.commit()


def test_handle_checkout_completed_ignores_non_consultant_product(
        app, user_a):
    """If metadata.product != 'consultant_booking', the webhook must NOT
    create a Booking row. The same Stripe account also carries X1 paywall
    events; the two handlers must not double-process each other's events.
    """
    from fiesta.consultant.routes import _handle_checkout_completed
    from fiesta.consultant.models import Booking, register_models
    register_models()
    from app import db

    fake_event = {
        "id": "evt_pytest_other_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_pytest_other_001",
                "payment_intent": "pi_pytest_other_001",
                "amount_total": 250000,
                "metadata": {
                    "user_id": str(user_a.id),
                    "product": "self_file",  # NOT consultant_booking
                    "tier": "self_file",
                },
            }
        },
    }
    with app.app_context():
        _handle_checkout_completed(fake_event)
        b = Booking.query.filter_by(
            stripe_payment_intent_id="pi_pytest_other_001"
        ).first()
        assert b is None, (
            "Consultant handler must ignore non-consultant_booking products"
        )


def test_handle_charge_refunded_flips_booking_to_refunded(
        app, booking_factory):
    from fiesta.consultant.routes import _handle_charge_refunded
    from fiesta.consultant.models import Booking, register_models
    register_models()
    from app import db

    b = booking_factory(
        stripe_payment_intent_id="pi_pytest_refund_001",
        stripe_session_id="cs_pytest_refund_001",
        amount_paid_lkr=5000,
    )
    fake_event = {
        "id": "evt_pytest_refund_001",
        "type": "charge.refunded",
        "data": {
            "object": {
                "payment_intent": "pi_pytest_refund_001",
            }
        },
    }
    with app.app_context():
        _handle_charge_refunded(fake_event)
        b2 = Booking.query.get(b.id)
        assert b2.status == "refunded"
        assert b2.refunded_at is not None
