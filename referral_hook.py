"""
referral_hook.py - Tier D4 / A3: invoice.paid -> referral credit side effect.

Single function ``apply_referral_credit_on_invoice_paid`` called from
webhooks/stripe_subscription.py inside the invoice.paid handler.

Flow
----
1. The referee (new user) just paid their first invoice. We're handed their
   user_id + stripe_subscription_id.
2. Look up a pending ReferralRedemption for this referee. If none, exit -
   not a referred user.
3. Mark redemption.paid_at = now + redemption.referee_subscription_id =
   the sub id (for audit).
4. Resolve the REFERRER's Stripe customer id from their Subscription row.
   If they have no Stripe customer (e.g. legacy one-time payer), log + exit
   without credit. We can't attach a coupon to a non-customer.
5. Create a one-off Stripe coupon (percent_off=20, duration='once',
   max_redemptions=1).
6. ``stripe.Customer.modify(coupon=...)`` to attach to the referrer's
   customer - Stripe auto-applies this on their next invoice.
7. Mark redemption.referrer_credit_applied_at = now +
   redemption.referrer_coupon_id = coupon.id.
8. Telegram alert (best-effort) for ops visibility.

Hard guarantees
---------------
* Never raises. The webhook caller wraps us in try/except too, but we don't
  rely on that.
* Idempotent. If invoice.paid is re-delivered, the redemption row's
  ``referrer_credit_applied_at`` is non-NULL on second call -> exit.
* No DB write if the Stripe coupon side fails. The redemption stays in
  paid_at-set / credit_applied-NULL state and can be retried.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from events import emit as emit_analytics_event

log = logging.getLogger(__name__)


def apply_referral_credit_on_invoice_paid(
    *,
    referee_user_id: Optional[int],
    stripe_subscription_id: str,
) -> bool:
    """Return True iff a referral credit was newly applied. False otherwise
    (no pending redemption / already credited / Stripe error / not a
    referred user)."""
    if not referee_user_id:
        return False

    try:
        from referral_models import (
            find_pending_redemption_for_referee, ReferralCode,
            ReferralRedemption, REFERRAL_DISCOUNT_PERCENT,
            REFERRAL_COUPON_DURATION, COUPON_NAME_PREFIX,
        )
    except Exception as exc:
        log.warning("referral_hook: import failed: %s", exc)
        return False

    if ReferralCode is None or ReferralRedemption is None:
        log.debug("referral_hook: referral models not registered - skip")
        return False

    redemption = find_pending_redemption_for_referee(referee_user_id)
    if redemption is None:
        # Not a referred user (or already paid earlier).
        return False

    if redemption.referrer_credit_applied_at is not None:
        # Idempotent re-delivery.
        return False

    from app import db

    # Stamp paid_at + the sub id even before Stripe coupon side effect.
    # If the coupon application fails we leave the row in this state and
    # can retry from a small admin task.
    redemption.paid_at = datetime.utcnow()
    if stripe_subscription_id:
        redemption.referee_subscription_id = stripe_subscription_id
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log.warning(
            "referral_hook: failed to stamp paid_at on redemption=%s: %s",
            redemption.id, exc,
        )
        return False

    # Resolve the referrer's Stripe customer id.
    code_row = ReferralCode.query.get(redemption.code_id)
    if code_row is None:
        log.warning(
            "referral_hook: code_id=%s not found for redemption=%s",
            redemption.code_id, redemption.id,
        )
        return False
    referrer_user_id = code_row.user_id

    referrer_customer_id = _resolve_referrer_stripe_customer(referrer_user_id)
    if not referrer_customer_id:
        log.info(
            "referral_hook: referrer user=%s has no Stripe customer - "
            "redemption=%s paid but no credit applied",
            referrer_user_id, redemption.id,
        )
        emit_analytics_event(
            "referral_credit_skipped_no_customer",
            user_id=referrer_user_id,
            payload={
                "redemption_id": redemption.id,
                "referee_user_id": referee_user_id,
            },
            source="hook:referral.invoice_paid",
        )
        return False

    # Stripe coupon side-effect.
    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("referral_hook: stripe SDK not installed - skip credit")
        return False

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        log.warning("referral_hook: STRIPE_SECRET_KEY not set - skip credit")
        return False
    stripe.api_key = secret_key

    coupon_name = (
        f"{COUPON_NAME_PREFIX}_{REFERRAL_DISCOUNT_PERCENT}_R{redemption.id}"
    )
    try:
        coupon = stripe.Coupon.create(
            percent_off=REFERRAL_DISCOUNT_PERCENT,
            duration=REFERRAL_COUPON_DURATION,
            max_redemptions=1,
            name=coupon_name,
            metadata={
                "fiesta_redemption_id": str(redemption.id),
                "fiesta_referrer_user_id": str(referrer_user_id),
                "fiesta_referee_user_id": str(referee_user_id),
            },
        )
    except Exception as exc:
        log.warning(
            "referral_hook: Stripe Coupon.create failed for redemption=%s: %s",
            redemption.id, exc,
        )
        return False

    coupon_id = getattr(coupon, "id", None) or (
        coupon.get("id") if isinstance(coupon, dict) else None
    )
    if not coupon_id:
        log.warning(
            "referral_hook: Stripe coupon returned no id for redemption=%s",
            redemption.id,
        )
        return False

    try:
        stripe.Customer.modify(referrer_customer_id, coupon=coupon_id)
    except Exception as exc:
        log.warning(
            "referral_hook: Customer.modify failed for customer=%s "
            "coupon=%s: %s",
            referrer_customer_id[:32], coupon_id, exc,
        )
        # Coupon exists but not attached; leave referrer_credit_applied_at
        # NULL so we can retry. The coupon will be garbage-collected after
        # 30 days of non-use per Stripe defaults.
        return False

    redemption.referrer_credit_applied_at = datetime.utcnow()
    redemption.referrer_coupon_id = coupon_id
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        log.warning(
            "referral_hook: failed to stamp credit_applied on redemption=%s: %s",
            redemption.id, exc,
        )
        return False

    log.info(
        "referral_hook: applied %s%% credit to referrer=%s "
        "(coupon=%s, redemption=%s)",
        REFERRAL_DISCOUNT_PERCENT, referrer_user_id, coupon_id, redemption.id,
    )

    emit_analytics_event(
        "referral_credit_applied",
        user_id=referrer_user_id,
        payload={
            "redemption_id": redemption.id,
            "referee_user_id": referee_user_id,
            "stripe_coupon_id": coupon_id,
            "stripe_customer_id": referrer_customer_id,
            "discount_percent": REFERRAL_DISCOUNT_PERCENT,
        },
        source="hook:referral.invoice_paid",
    )

    # Telegram alert (best-effort).
    try:
        from ops_alerts import send_alert
        send_alert(
            severity="INFO",
            title="Referral credit applied",
            body=(
                f"Referrer user={referrer_user_id} earned "
                f"{REFERRAL_DISCOUNT_PERCENT}% off next invoice. "
                f"Coupon={coupon_id}, redemption={redemption.id}."
            ),
        )
    except Exception as exc:
        log.debug("referral_hook: telegram alert skipped: %s", exc)

    return True


def _resolve_referrer_stripe_customer(referrer_user_id: int) -> Optional[str]:
    """Find the referrer's Stripe customer id by checking their most recent
    paywall_subscription row. Returns None if they have no Stripe customer
    (which is possible for legacy one-time payers who never went through
    the subscription path).
    """
    try:
        from fiesta.paywall.models import Subscription
        if Subscription is None:
            return None
        row = (
            Subscription.query
            .filter(Subscription.user_id == referrer_user_id)
            .filter(Subscription.stripe_customer_id.isnot(None))
            .order_by(Subscription.purchased_at.desc())
            .first()
        )
        if row is None or not row.stripe_customer_id:
            return None
        return row.stripe_customer_id
    except Exception as exc:
        log.debug(
            "referral_hook: customer lookup failed for user=%s: %s",
            referrer_user_id, exc,
        )
        return None


__all__ = ["apply_referral_credit_on_invoice_paid"]
