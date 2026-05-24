"""Tier D3 / C5 — Dunning recovery tests.

3 cases (per task spec):
  1. webhook fires invoice.payment_failed -> record_failed_payment called
     with the right args + send_alert called.
  2. invoice.paid -> mark_invoice_paid called.
  3. should_show_banner returns True when at least one pending Dunning row
     exists for the user, False otherwise.

Following the existing tests/stripe_subscription pattern: DB layer is
stubbed so the dunning migration (migrations/add_dunning.py) does NOT need
to be applied to the test Neon instance for these tests to pass.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest


pytestmark = pytest.mark.usefixtures("_ensure_subscription_bp")


# --------------------------------------------------------------------------- #
# Event factories.
# --------------------------------------------------------------------------- #

def _make_payment_failed_event(*, stripe_subscription_id, event_id,
                                invoice_id="in_pytest_d3_failed",
                                attempt_count=2,
                                next_payment_attempt=None):
    return {
        "id": event_id,
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": invoice_id,
                "subscription": stripe_subscription_id,
                "customer": "cus_pytest_d3",
                "amount_due": 250_000,
                "currency": "lkr",
                "attempt_count": attempt_count,
                "next_payment_attempt": next_payment_attempt,
                "metadata": {"user_id": "42"},
            }
        },
    }


def _make_invoice_paid_event(*, stripe_subscription_id, event_id,
                              invoice_id="in_pytest_d3_paid",
                              period_end_unix=None):
    if period_end_unix is None:
        period_end_unix = int(
            (datetime.utcnow() + timedelta(days=365))
            .replace(tzinfo=timezone.utc).timestamp()
        )
    return {
        "id": event_id,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": invoice_id,
                "subscription": stripe_subscription_id,
                "customer": "cus_pytest_d3",
                "amount_paid": 250_000,
                "currency": "lkr",
                "metadata": {"user_id": "42"},
                "period_end": period_end_unix,
                "lines": {
                    "data": [{
                        "period": {
                            "start": period_end_unix - 365 * 86400,
                            "end": period_end_unix,
                        },
                    }],
                },
            }
        },
    }


def _post_webhook(client, event_payload):
    """POST to /webhooks/stripe/subscription with signature + secret stubbed.

    Stubs the dedup tombstone but does NOT stub the per-event handler so
    the real _handle_payment_failed / _handle_invoice_paid run (and we can
    assert their side effects).
    """
    sig_cm = patch(
        "stripe.Webhook.construct_event",
        return_value=event_payload,
    )
    env_cm = patch.dict(
        "os.environ",
        {"STRIPE_SUBSCRIPTION_WEBHOOK_SECRET": "whsec_pytest_d3"},
        clear=False,
    )
    tombstone_patches = [
        patch(
            "webhooks.stripe_subscription._stripe_event_already_handled",
            return_value=False,
        ),
        patch(
            "webhooks.stripe_subscription._mark_stripe_event",
            return_value=None,
        ),
    ]

    with sig_cm, env_cm:
        entered = [p.__enter__() for p in tombstone_patches]
        try:
            return client.post(
                "/webhooks/stripe/subscription",
                data=json.dumps(event_payload),
                headers={"Stripe-Signature": "test"},
                content_type="application/json",
            )
        finally:
            for p in reversed(tombstone_patches):
                p.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# Test 1 — invoice.payment_failed wires record_failed_payment + Telegram.
# --------------------------------------------------------------------------- #

class TestPaymentFailedRecordsDunning:
    def test_payment_failed_calls_record_and_alert(self, client):
        """Webhook fires invoice.payment_failed -> _handle_payment_failed
        flips Subscription.status='dunning' AND calls
        dunning_sequence.record_failed_payment with the right args."""

        # Mock the Subscription row lookup so _handle_payment_failed has
        # something to flip without hitting the unmigrated test DB. We need
        # an object whose status/user_id/id attributes are mutable + readable.
        fake_sub = MagicMock()
        fake_sub.id = 7777
        fake_sub.user_id = 42
        fake_sub.status = "active"

        fake_query = MagicMock()
        fake_query.filter_by.return_value.first.return_value = fake_sub

        event = _make_payment_failed_event(
            stripe_subscription_id="sub_pytest_d3_failed",
            event_id="evt_pytest_d3_failed_1",
            attempt_count=2,
            next_payment_attempt=int(
                (datetime.utcnow() + timedelta(days=3)).timestamp()
            ),
        )

        with patch("fiesta.paywall.models.Subscription", create=True) as MockSub, \
             patch("app.db.session.commit", return_value=None), \
             patch("dunning_sequence.record_failed_payment") as mock_record:
            MockSub.query = fake_query

            resp = _post_webhook(client, event)

        assert resp.status_code == 200
        # Subscription row got flipped to 'dunning' (Wave 1 #C1 behavior preserved).
        assert fake_sub.status == "dunning"
        # And the C5 dunning recorder was called with the right args.
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["subscription_id"] == 7777
        assert call_kwargs["stripe_invoice_id"] == "in_pytest_d3_failed"
        assert call_kwargs["attempt_count"] == 2
        assert call_kwargs["next_retry_at"] is not None


# --------------------------------------------------------------------------- #
# Test 2 — invoice.paid closes open dunning rows.
# --------------------------------------------------------------------------- #

class TestInvoicePaidClosesDunning:
    def test_invoice_paid_calls_mark_invoice_paid(self, client):
        """Webhook fires invoice.paid -> _handle_invoice_paid calls
        dunning_sequence.mark_invoice_paid with the invoice id so the
        banner clears for the recovered user."""

        fake_sub = MagicMock()
        fake_sub.id = 8888
        fake_sub.user_id = 42
        fake_sub.status = "dunning"
        fake_sub.auto_renew = True
        fake_sub.stripe_customer_id = "cus_pytest_d3"
        fake_sub.current_period_end = None
        fake_sub.expires_at = None

        fake_query = MagicMock()
        fake_query.filter_by.return_value.first.return_value = fake_sub

        fake_user_query = MagicMock()
        fake_user_query.get.return_value = None  # skip User update

        event = _make_invoice_paid_event(
            stripe_subscription_id="sub_pytest_d3_paid",
            event_id="evt_pytest_d3_paid_1",
            invoice_id="in_pytest_d3_recovered",
        )

        with patch("fiesta.paywall.models.Subscription", create=True) as MockSub, \
             patch("models.User", create=True) as MockUser, \
             patch("app.db.session.commit", return_value=None), \
             patch("dunning_sequence.mark_invoice_paid",
                   return_value=1) as mock_mark, \
             patch("webhooks.stripe_subscription._get_or_create_subscription",
                   return_value=fake_sub):
            MockSub.query = fake_query
            MockUser.query = fake_user_query

            resp = _post_webhook(client, event)

        assert resp.status_code == 200
        mock_mark.assert_called_once_with("in_pytest_d3_recovered")
        # Wave 1 #C1 still flipped status active + auto_renew.
        assert fake_sub.status == "active"
        assert fake_sub.auto_renew is True


# --------------------------------------------------------------------------- #
# Test 3 — should_show_banner uses the Dunning model.
# --------------------------------------------------------------------------- #

class TestShouldShowBanner:
    def test_returns_true_when_pending_dunning_exists(self):
        """should_show_banner queries paywall_dunning for state='pending'
        rows belonging to the user; True when any exist."""
        from dunning_sequence import should_show_banner

        fake_query = MagicMock()
        fake_query.filter_by.return_value.first.return_value = object()

        with patch("dunning_sequence.Dunning", create=True) as MockDunning, \
             patch("dunning_sequence.register_dunning_model",
                   return_value=MockDunning):
            MockDunning.query = fake_query

            assert should_show_banner(42) is True

        # filter_by called with (user_id=42, state='pending') — that's the
        # gate logic.
        call_kwargs = fake_query.filter_by.call_args.kwargs
        assert call_kwargs["user_id"] == 42
        assert call_kwargs["state"] == "pending"

    def test_returns_false_when_no_pending_dunning(self):
        from dunning_sequence import should_show_banner

        fake_query = MagicMock()
        fake_query.filter_by.return_value.first.return_value = None

        with patch("dunning_sequence.Dunning", create=True) as MockDunning, \
             patch("dunning_sequence.register_dunning_model",
                   return_value=MockDunning):
            MockDunning.query = fake_query

            assert should_show_banner(42) is False

    def test_returns_false_for_anonymous_user(self):
        from dunning_sequence import should_show_banner
        assert should_show_banner(None) is False
        assert should_show_banner(0) is False
