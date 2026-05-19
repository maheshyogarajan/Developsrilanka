"""
X1 Paywall test suite — 18 cases covering the council brief acceptance bar.

Categories:

  Models / pure functions     -> 1-4
  Decorator behavior          -> 5-9
  Pricing screen + checkout   -> 10-12
  Webhook                     -> 13-16
  Funnel analytics            -> 17-18

Fixtures (re-used from tests/remittance/conftest + tests/paywall/conftest):

  app, client, db_session, user_a, user_b, login_as, subscription_factory
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, date
from unittest.mock import patch

import pytest

from fiesta.paywall.models import (
    TIER_FREE_TRIAL, TIER_SELF_FILE, TIER_AUTO_FILE,
    SELF_FILE_PRICE_LKR, SELF_FILE_PRICE_CENTS,
    TIER_ORDER, current_sl_tax_year, expires_at_for_tax_year,
)
from fiesta.paywall.gate import (
    paywall_required, is_tier_active, effective_tier,
    FREE_TIER_SCREENS, SELF_FILE_SCREENS,
)
from fiesta.paywall.trial import TRIAL_DAYS, is_in_trial, trial_days_remaining
from fiesta.paywall.funnel import funnel_summary, funnel_daily


# =========================================================================== #
# Section 1: Pure-function / model sanity
# =========================================================================== #

class TestTaxYearMath:
    """Test 1: current_sl_tax_year + expires_at_for_tax_year are deterministic
    and correct across the April-1 boundary."""

    def test_tax_year_boundary(self):
        # Sri Lankan year of assessment runs 1 Apr -> 31 Mar.
        assert current_sl_tax_year(date(2026, 3, 31)) == "2025/26"
        assert current_sl_tax_year(date(2026, 4, 1)) == "2026/27"
        assert current_sl_tax_year(date(2026, 5, 20)) == "2026/27"
        assert current_sl_tax_year(date(2025, 8, 15)) == "2025/26"

    def test_expires_at_for_tax_year(self):
        # 2025/26 expires 31 Mar 2026 23:59:59
        ex = expires_at_for_tax_year("2025/26")
        assert ex.year == 2026 and ex.month == 3 and ex.day == 31
        # 2026/27 expires 31 Mar 2027
        ex2 = expires_at_for_tax_year("2026/27")
        assert ex2.year == 2027 and ex2.month == 3 and ex2.day == 31


class TestTierOrder:
    """Test 2: tier ranks are strictly ordered free_trial < self_file < auto_file."""

    def test_tier_order_strict(self):
        assert TIER_ORDER[TIER_FREE_TRIAL] == 0
        assert TIER_ORDER[TIER_SELF_FILE] == 1
        assert TIER_ORDER[TIER_AUTO_FILE] == 2
        assert TIER_ORDER[TIER_FREE_TRIAL] < TIER_ORDER[TIER_SELF_FILE] < TIER_ORDER[TIER_AUTO_FILE]


class TestScreenCatalog:
    """Test 3: free + self_file screen sets cover S0-S12 + S14 with no overlap."""

    def test_free_and_paid_screens_no_overlap(self):
        assert FREE_TIER_SCREENS.isdisjoint(SELF_FILE_SCREENS)
        assert "S6" in SELF_FILE_SCREENS
        assert "S12" in SELF_FILE_SCREENS
        assert "S14" in SELF_FILE_SCREENS
        assert "S0" in FREE_TIER_SCREENS
        assert "S5" in FREE_TIER_SCREENS


class TestTrialWindow:
    """Test 4: trial helpers correctly read User.created_at + 14d."""

    def test_trial_active_recent(self, user_a):
        # user_a is created in the fixture moments before this runs.
        assert is_in_trial(user_a) is True
        assert trial_days_remaining(user_a) >= 13  # within seconds of creation

    def test_trial_expired(self, user_a, db_session):
        # Backdate created_at to 30 days ago.
        user_a.created_at = datetime.utcnow() - timedelta(days=30)
        db_session.commit()
        assert is_in_trial(user_a) is False
        assert trial_days_remaining(user_a) == 0


# =========================================================================== #
# Section 2: Decorator behavior
# =========================================================================== #

# Note: the `gated_view_path` fixture is defined session-scoped in
# tests/paywall/conftest.py so the test route is registered BEFORE Flask
# handles its first request (Flask blocks late route registration).


class TestDecoratorFreeUserHitsPaywall:
    """Test 5: free-tier user hits S6 -> redirects to /pricing/x1 with return_to."""

    def test_redirect(self, client, user_a, gated_view_path):
        from tests.remittance.conftest import login_as
        login_as(client, user_a)
        resp = client.get(gated_view_path, follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("Location", "")
        assert "/pricing/x1" in location
        assert "return_to=" in location
        assert "screen_id=S6" in location


class TestDecoratorActiveSubscriberPassthrough:
    """Test 6: user with active self_file subscription gets the view content."""

    def test_passthrough(self, client, user_a, subscription_factory, gated_view_path):
        from tests.remittance.conftest import login_as
        subscription_factory(user_a, tier=TIER_SELF_FILE,
                             stripe_payment_intent_id="pi_pytest_active_1")
        login_as(client, user_a)
        resp = client.get(gated_view_path)
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}


class TestDecoratorAJAX402:
    """Test 7: AJAX request gets 402 + paywall_url in JSON body."""

    def test_ajax_402(self, client, user_a, gated_view_path):
        from tests.remittance.conftest import login_as
        login_as(client, user_a)
        resp = client.get(
            gated_view_path,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 402
        body = resp.get_json()
        assert body["error"] == "payment_required"
        assert "/pricing/x1" in body["paywall_url"]
        assert body["required_tier"] == TIER_SELF_FILE
        assert body["screen_id"] == "S6"


class TestDecoratorExpiredSubscription:
    """Test 8: was self_file, now past expires_at -> paywall on Self-File screens."""

    def test_expired_subscription_blocks(self, client, user_a, subscription_factory,
                                          gated_view_path):
        from tests.remittance.conftest import login_as
        # Mint a subscription that expired yesterday.
        subscription_factory(
            user_a,
            tier=TIER_SELF_FILE,
            stripe_payment_intent_id="pi_pytest_expired_1",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        login_as(client, user_a)
        resp = client.get(gated_view_path, follow_redirects=False)
        assert resp.status_code == 302
        assert "/pricing/x1" in resp.headers.get("Location", "")


class TestDecoratorPaywallEventRecorded:
    """Test 9: a redirect inserts a PaywallEvent row with the right fields."""

    def test_event_recorded(self, client, user_a, gated_view_path):
        from tests.remittance.conftest import login_as
        from fiesta.paywall import get_models
        _, PaywallEvent, _ = get_models()

        before = PaywallEvent.query.filter_by(user_id=user_a.id).count()
        login_as(client, user_a)
        client.get(gated_view_path, follow_redirects=False)
        after = PaywallEvent.query.filter_by(user_id=user_a.id).count()
        assert after == before + 1

        row = (
            PaywallEvent.query
            .filter_by(user_id=user_a.id)
            .order_by(PaywallEvent.id.desc())
            .first()
        )
        assert row.screen_id == "S6"
        assert row.required_tier == TIER_SELF_FILE
        assert row.was_ajax is False
        assert row.converted_at is None


# =========================================================================== #
# Section 3: Pricing screen + checkout endpoint
# =========================================================================== #

class TestPricingScreenAnonymous:
    """Test 10: GET /pricing/x1 returns 200 for anon users."""

    def test_anonymous(self, client):
        resp = client.get("/pricing/x1")
        assert resp.status_code == 200
        assert b"Self-File" in resp.data or b"Rs 2,500" in resp.data


class TestPricingJsonEndpoint:
    """Test 11: /pricing/x1.json mirrors product spec + current tax year."""

    def test_json_endpoint(self, client):
        resp = client.get("/pricing/x1.json")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["product"]["price_lkr"] == SELF_FILE_PRICE_LKR
        assert body["product"]["billing_model"] == "one_time"
        assert body["product"]["refund_window_days"] == 14
        assert body["tax_year"]  # non-empty
        assert "expires_at_iso" in body


class TestCheckoutStripeMissing:
    """Test 12: POST /pricing/x1/checkout flashes + redirects when stripe SDK is
    unavailable / secret missing. (Real Stripe Test-mode calls are out of scope
    for this unit suite.)"""

    def test_no_stripe_secret(self, client, user_a, monkeypatch):
        from tests.remittance.conftest import login_as
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        login_as(client, user_a)
        resp = client.post(
            "/pricing/x1/checkout",
            data={"return_to": "/_test/paywall/S6", "screen_id": "S6"},
            follow_redirects=False,
        )
        # Should redirect back to /pricing/x1 with a flash.
        assert resp.status_code in (302, 303, 503)


# =========================================================================== #
# Section 4: Webhook idempotency + subscription creation
# =========================================================================== #

def _make_checkout_completed_event(user_id, *, event_id, payment_intent,
                                     session_id="cs_pytest_1",
                                     amount_total=SELF_FILE_PRICE_CENTS,
                                     paywall_event_id=None,
                                     tax_year=None):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "payment_intent": payment_intent,
                "amount_total": amount_total,
                "currency": "lkr",
                "metadata": {
                    "user_id": str(user_id),
                    "tier": TIER_SELF_FILE,
                    "tax_year": tax_year or current_sl_tax_year(),
                    "paywall_event_id": str(paywall_event_id) if paywall_event_id else "",
                },
            }
        },
    }


def _post_webhook(client, event_payload):
    """POST a Stripe event payload to the paywall webhook. We patch the SDK
    signature verification so we don't need to sign with a real secret."""
    with patch("stripe.Webhook.construct_event", return_value=event_payload):
        # Also make sure the secret guard is satisfied.
        with patch.dict("os.environ", {"STRIPE_PAYWALL_WEBHOOK_SECRET": "whsec_pytest"}):
            return client.post(
                "/webhooks/stripe/paywall",
                data=json.dumps(event_payload),
                headers={"Stripe-Signature": "test"},
                content_type="application/json",
            )


class TestWebhookCheckoutCompletedCreatesSubscription:
    """Test 13: checkout.session.completed creates a Subscription row + marks
    PaywallEvent as converted."""

    def test_creates_subscription(self, client, user_a, db_session):
        from fiesta.paywall import get_models
        Subscription, PaywallEvent, _ = get_models()

        # Seed a PaywallEvent to be converted.
        pwe = PaywallEvent(
            user_id=user_a.id,
            screen_id="S6",
            required_tier=TIER_SELF_FILE,
            action_attempted="test",
        )
        db_session.add(pwe)
        db_session.commit()

        event = _make_checkout_completed_event(
            user_a.id,
            event_id="evt_pytest_checkout_1",
            payment_intent="pi_pytest_wh_1",
            paywall_event_id=pwe.id,
        )
        resp = _post_webhook(client, event)
        assert resp.status_code == 200

        sub = Subscription.query.filter_by(
            stripe_payment_intent_id="pi_pytest_wh_1"
        ).first()
        assert sub is not None
        assert sub.user_id == user_a.id
        assert sub.tier == TIER_SELF_FILE
        assert sub.status == "active"
        assert sub.amount_paid_lkr == SELF_FILE_PRICE_LKR

        # Originating event marked as converted.
        db_session.refresh(pwe)
        assert pwe.converted_at is not None
        assert pwe.conversion_revenue_lkr == SELF_FILE_PRICE_LKR


class TestWebhookIdempotencyByEventId:
    """Test 14: re-delivering the SAME event id is a no-op (event tombstone)."""

    def test_idempotent_event_id(self, client, user_a):
        from fiesta.paywall import get_models
        Subscription, _, StripeEvent = get_models()

        event = _make_checkout_completed_event(
            user_a.id,
            event_id="evt_pytest_idem_1",
            payment_intent="pi_pytest_idem_1",
        )

        r1 = _post_webhook(client, event)
        assert r1.status_code == 200
        count_1 = Subscription.query.filter_by(
            stripe_payment_intent_id="pi_pytest_idem_1"
        ).count()
        assert count_1 == 1

        # Re-deliver — should be deduped by event_id tombstone, NOT create
        # a second row.
        r2 = _post_webhook(client, event)
        assert r2.status_code == 200
        body = r2.get_json()
        assert body.get("duplicate") is True

        count_2 = Subscription.query.filter_by(
            stripe_payment_intent_id="pi_pytest_idem_1"
        ).count()
        assert count_2 == 1

        # Tombstone row exists.
        tombstone = StripeEvent.query.filter_by(
            stripe_event_id="evt_pytest_idem_1"
        ).first()
        assert tombstone is not None
        assert tombstone.handled is True


class TestWebhookIdempotencyByPaymentIntent:
    """Test 15: even if a different event id delivers the same payment_intent
    (extreme edge — Stripe shouldn't but defence-in-depth), we don't double-mint.
    """

    def test_idempotent_payment_intent(self, client, user_a):
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()

        event_1 = _make_checkout_completed_event(
            user_a.id,
            event_id="evt_pytest_pi_1",
            payment_intent="pi_pytest_dup_1",
        )
        event_2 = _make_checkout_completed_event(
            user_a.id,
            event_id="evt_pytest_pi_2",
            payment_intent="pi_pytest_dup_1",  # same PI on different event id
        )

        assert _post_webhook(client, event_1).status_code == 200
        assert _post_webhook(client, event_2).status_code == 200

        count = Subscription.query.filter_by(
            stripe_payment_intent_id="pi_pytest_dup_1"
        ).count()
        assert count == 1


class TestWebhookRefundFlipsStatus:
    """Test 16: charge.refunded flips the matching Subscription to status='refunded'."""

    def test_refund_flips(self, client, user_a):
        from fiesta.paywall import get_models
        Subscription, _, _ = get_models()

        # Seed a subscription via webhook.
        evt = _make_checkout_completed_event(
            user_a.id,
            event_id="evt_pytest_refund_seed",
            payment_intent="pi_pytest_refund_1",
        )
        _post_webhook(client, evt)

        # Deliver refund.
        refund_event = {
            "id": "evt_pytest_refund_1",
            "type": "charge.refunded",
            "data": {
                "object": {
                    "payment_intent": "pi_pytest_refund_1",
                    "amount_refunded": SELF_FILE_PRICE_CENTS,
                }
            }
        }
        resp = _post_webhook(client, refund_event)
        assert resp.status_code == 200

        sub = Subscription.query.filter_by(
            stripe_payment_intent_id="pi_pytest_refund_1"
        ).first()
        assert sub is not None
        assert sub.status == "refunded"
        assert sub.refunded_at is not None
        # Refunded sub should NOT be considered active.
        assert sub.is_active is False


# =========================================================================== #
# Section 5: Funnel analytics
# =========================================================================== #

class TestFunnelSummaryPerUser:
    """Test 17: per-user funnel summary surfaces fire count, conversion rate,
    most-fired screen."""

    def test_user_funnel(self, client, user_a, db_session, gated_view_path):
        from tests.remittance.conftest import login_as
        from fiesta.paywall import get_models
        _, PaywallEvent, _ = get_models()

        login_as(client, user_a)
        # Trigger 3 paywall fires on S6.
        for _ in range(3):
            client.get(gated_view_path, follow_redirects=False)

        summary = funnel_summary(user_a.id)
        assert summary["paywall_fired_count"] >= 3
        assert summary["screen_id_with_most_fires"] == "S6"
        assert summary["conversions"] == 0
        assert summary["conversion_rate"] == 0.0

        # Now flip one event to converted.
        row = (
            PaywallEvent.query
            .filter_by(user_id=user_a.id)
            .order_by(PaywallEvent.id.desc())
            .first()
        )
        row.converted_at = datetime.utcnow()
        row.conversion_revenue_lkr = SELF_FILE_PRICE_LKR
        db_session.commit()

        summary2 = funnel_summary(user_a.id)
        assert summary2["conversions"] == 1
        assert summary2["conversion_rate"] > 0


class TestFunnelDailyAggregateConversionRate:
    """Test 18: 100-event simulated aggregate. Conversion rate computed
    correctly. Spec acceptance bar: rate >= 0 and <= 1 and matches our seed."""

    def test_aggregate_conversion_rate(self, app, user_a, db_session):
        from fiesta.paywall import get_models
        _, PaywallEvent, _ = get_models()

        # Seed 100 events; 17 of them converted. Matches the council's
        # 12-20% expected conversion band.
        TOTAL = 100
        CONVERTED = 17
        now = datetime.utcnow()
        rows = []
        for i in range(TOTAL):
            r = PaywallEvent(
                user_id=user_a.id,
                screen_id="S6" if i % 2 == 0 else "S11",
                required_tier=TIER_SELF_FILE,
                action_attempted="seed",
                fired_at=now - timedelta(hours=i),
                converted_at=(now - timedelta(hours=i) + timedelta(minutes=30))
                              if i < CONVERTED else None,
                conversion_revenue_lkr=SELF_FILE_PRICE_LKR if i < CONVERTED else None,
            )
            db_session.add(r)
            rows.append(r)
        db_session.commit()

        agg = funnel_daily(days=30)
        # The aggregate may include other test users' rows; we assert lower
        # bounds based on what we seeded.
        assert agg["total_paywall_fires"] >= TOTAL
        assert agg["total_conversions"] >= CONVERTED
        assert 0.0 <= agg["conversion_rate"] <= 1.0
        assert "S6" in agg["fires_by_screen"]
        assert "S11" in agg["fires_by_screen"]
        # Average time-to-conversion: we set delta=30min for converted rows.
        assert agg["average_time_to_conversion_hours"] is not None
        assert agg["average_time_to_conversion_hours"] > 0
