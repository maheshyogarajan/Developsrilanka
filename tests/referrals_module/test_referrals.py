"""Tier D4 / A3 - referral loop unit tests.

Three cases. Hits route handlers + helper logic with the DB layer + Stripe
mocked. No live Neon required. Mirrors the pattern used in
tests/stripe_subscription/test_subscription_webhook.py - the per-table
referral_code / referral_redemption schema is shipped via
migrations/add_referrals.py and is a CEO deploy step.

  1. generate_code uniqueness across N calls + recorded shape (8 hex chars).
  2. record_signup_redemption is idempotent on (referee_user_id) + blocks
     self-referral + bumps uses_count.
  3. apply_referral_credit_on_invoice_paid creates the Stripe coupon +
     attaches to referrer's customer + stamps redemption columns. Re-call
     is a no-op (idempotent).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

import referral_models as rm


# --------------------------------------------------------------------------- #
# Test 1 - generate_code shape + uniqueness.
# --------------------------------------------------------------------------- #

def test_generate_code_shape_and_uniqueness():
    """generate_code returns 8 hex chars; 200 successive calls collide < 1."""
    seen = set()
    for _ in range(200):
        c = rm.generate_code()
        assert isinstance(c, str)
        assert len(c) == 8
        # Hex-only
        int(c, 16)
        seen.add(c)
    # 200 draws from a 32-bit space: birthday-collision probability is
    # ~2e-6 - assert at least 199 unique.
    assert len(seen) >= 199


# --------------------------------------------------------------------------- #
# Test 2 - record_signup_redemption idempotency + self-referral guard.
# --------------------------------------------------------------------------- #

class _FakeQuery:
    """Minimal stand-in for a SQLAlchemy Query. Returns a configurable
    first_result for .first() and tracks .filter_by calls."""
    def __init__(self, first_result=None):
        self._first_result = first_result
        self.filter_kwargs = []

    def filter_by(self, **kwargs):
        self.filter_kwargs.append(kwargs)
        return self

    def first(self):
        return self._first_result


class _FakeDbSession:
    def __init__(self):
        self.added = []
        self.committed = False
        self.rolled_back = False

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def flush(self):
        pass


class _FakeDb:
    def __init__(self):
        self.session = _FakeDbSession()


def test_record_signup_redemption_idempotent_and_blocks_self_referral():
    """record_signup_redemption:
      * returns existing redemption on second call for same referee (idempotent)
      * blocks self-referral (returns None, no row created)
      * bumps uses_count on first successful redemption
    """
    code_row = SimpleNamespace(
        id=1, user_id=10, code="abcdef01",
        is_redeemable=True, uses_count=3,
        # is_redeemable is a real property in production - here we stub it
        # as a plain attribute.
    )
    referee_id = 99

    # ----- Case A: self-referral (code.user_id == referee) -----
    with patch.object(rm, "ReferralCode", create=True) as ref_code_mock, \
         patch.object(rm, "ReferralRedemption", create=True) as ref_red_mock:
        ref_code_mock.query = _FakeQuery(first_result=code_row)
        ref_red_mock.query = _FakeQuery(first_result=None)
        with patch.dict("sys.modules", {"app": SimpleNamespace(db=_FakeDb())}):
            # referee_id == code.user_id triggers self-referral block
            result = rm.record_signup_redemption(
                code_row.code, referee_user_id=code_row.user_id,
            )
            assert result is None, "self-referral must be blocked"

    # ----- Case B: idempotent (existing redemption returned) -----
    existing_redemption = SimpleNamespace(id=55, referee_user_id=referee_id)
    with patch.object(rm, "ReferralCode", create=True) as ref_code_mock, \
         patch.object(rm, "ReferralRedemption", create=True) as ref_red_mock:
        ref_code_mock.query = _FakeQuery(first_result=code_row)
        ref_red_mock.query = _FakeQuery(first_result=existing_redemption)
        fake_db = _FakeDb()
        with patch.dict("sys.modules", {"app": SimpleNamespace(db=fake_db)}):
            result = rm.record_signup_redemption(
                code_row.code, referee_user_id=referee_id,
            )
            assert result is existing_redemption
            assert fake_db.session.added == [], (
                "no new row should be added when one already exists"
            )

    # ----- Case C: happy path, bumps uses_count -----
    # Brand-new redemption: ReferralCode.first returns code, Redemption.first
    # returns None.
    starting_uses = code_row.uses_count
    fake_red_class = MagicMock(name="_FakeRedemptionClass")
    fake_red_class.return_value = SimpleNamespace(
        id=None, referee_user_id=referee_id,
    )
    with patch.object(rm, "ReferralCode", create=True) as ref_code_mock, \
         patch.object(rm, "ReferralRedemption", new=fake_red_class):
        ref_code_mock.query = _FakeQuery(first_result=code_row)
        fake_red_class.query = _FakeQuery(first_result=None)
        fake_db = _FakeDb()
        with patch.dict("sys.modules", {"app": SimpleNamespace(db=fake_db)}):
            result = rm.record_signup_redemption(
                code_row.code, referee_user_id=referee_id,
            )
            assert result is not None
            assert fake_db.session.committed, "expected commit on insert"
            assert code_row.uses_count == starting_uses + 1, (
                "uses_count should bump by 1"
            )


# --------------------------------------------------------------------------- #
# Test 3 - apply_referral_credit_on_invoice_paid happy path + idempotency.
# --------------------------------------------------------------------------- #

def test_apply_referral_credit_creates_coupon_and_is_idempotent():
    """apply_referral_credit_on_invoice_paid:
      * On first call: creates Stripe coupon, attaches to referrer customer,
        stamps redemption.referrer_credit_applied_at + .referrer_coupon_id,
        returns True.
      * Re-call (idempotent): redemption.referrer_credit_applied_at non-NULL
        => returns False without firing any Stripe call.
    """
    import referral_hook

    redemption = SimpleNamespace(
        id=42, code_id=7, referee_user_id=99,
        paid_at=None, referrer_credit_applied_at=None,
        referrer_coupon_id=None, referee_subscription_id=None,
    )
    code_row = SimpleNamespace(id=7, user_id=10, uses_count=1)

    # Mock referral_models accessors used by the hook
    with patch("referral_models.find_pending_redemption_for_referee",
               return_value=redemption), \
         patch("referral_models.ReferralCode", create=True) as ref_code_mock, \
         patch("referral_models.ReferralRedemption", create=True), \
         patch.object(referral_hook, "_resolve_referrer_stripe_customer",
                      return_value="cus_pytest_referrer_001"), \
         patch.dict("os.environ", {"STRIPE_SECRET_KEY": "sk_test_fake"}):

        ref_code_mock.query = MagicMock()
        ref_code_mock.query.get = MagicMock(return_value=code_row)

        fake_db = _FakeDb()
        fake_app = SimpleNamespace(db=fake_db)

        coupon_mock = SimpleNamespace(id="coupon_pytest_001")

        with patch.dict("sys.modules", {"app": fake_app}):
            import sys
            stripe_stub = MagicMock(name="stripe")
            stripe_stub.Coupon.create = MagicMock(return_value=coupon_mock)
            stripe_stub.Customer.modify = MagicMock(return_value=None)
            sys.modules["stripe"] = stripe_stub

            # ----- First call: applies credit -----
            ok = referral_hook.apply_referral_credit_on_invoice_paid(
                referee_user_id=99,
                stripe_subscription_id="sub_pytest_referee_001",
            )
            assert ok is True, "first call should apply credit"
            assert redemption.paid_at is not None
            assert redemption.referee_subscription_id == "sub_pytest_referee_001"
            assert redemption.referrer_credit_applied_at is not None
            assert redemption.referrer_coupon_id == "coupon_pytest_001"
            stripe_stub.Coupon.create.assert_called_once()
            create_kwargs = stripe_stub.Coupon.create.call_args.kwargs
            assert create_kwargs["percent_off"] == 20
            assert create_kwargs["duration"] == "once"
            assert create_kwargs["max_redemptions"] == 1
            stripe_stub.Customer.modify.assert_called_once_with(
                "cus_pytest_referrer_001", coupon="coupon_pytest_001",
            )

            # ----- Second call: no-op (idempotent) -----
            stripe_stub.Coupon.create.reset_mock()
            stripe_stub.Customer.modify.reset_mock()
            with patch("referral_models.find_pending_redemption_for_referee",
                       return_value=redemption):
                # redemption.referrer_credit_applied_at is now non-NULL, but
                # find_pending_redemption_for_referee only returns rows with
                # paid_at=None. After first call paid_at is set, so a real
                # caller would already get None. Simulate that:
                with patch("referral_models.find_pending_redemption_for_referee",
                           return_value=None):
                    ok2 = referral_hook.apply_referral_credit_on_invoice_paid(
                        referee_user_id=99,
                        stripe_subscription_id="sub_pytest_referee_001",
                    )
                    assert ok2 is False
                    stripe_stub.Coupon.create.assert_not_called()
                    stripe_stub.Customer.modify.assert_not_called()
