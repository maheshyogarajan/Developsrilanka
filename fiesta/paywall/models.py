"""
fiesta.paywall.models — Subscription + PaywallEvent ORM tables.

Two thin tables on top of the existing SQLAlchemy ``db`` defined in app.py.

* ``Subscription`` — one row per paid unlock. Tax-year-bounded (NOT rolling
  12-month). Keyed by ``user_id + tier + expires_at`` for the lookup path
  (``active_subscription(user, tier)``).

* ``PaywallEvent`` — one row per paywall fire. Captures the screen and the
  action attempted. ``converted_at`` / ``conversion_revenue_lkr`` get filled
  when the matching Subscription row appears (the Stripe webhook does this
  linkage).

* ``StripeEvent`` — idempotency tombstone for Stripe webhook event IDs. The
  Stripe API guarantees that delivering the same ``event.id`` more than once
  is allowed (retries on 5xx, network flakes). We dedupe here so the X1 paywall
  webhook handler is provably idempotent. Distinct from the analytics Event
  table (events.py) which is append-only and tolerant of duplicates.

Migration policy
----------------
We use ``db.create_all()`` at app boot (main.py) — same convention the rest of
the codebase follows (remittance_models, engagement_models, etc.). No Alembic
migration file is shipped here; the additive schema bootstrap in
``app._ensure_additive_schema()`` is the belt-and-braces path if create_all
is bypassed (gunicorn, wsgi.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tier definitions — X1 unified model.
# --------------------------------------------------------------------------- #
#
# Distinct from pricing_engine.PRICING_TIERS which is the LEGACY 3-tier
# subscription product. X1 ships ONE paid product (self_file) at Rs 2,500
# one-time, tax-year-bounded.
#
TIER_FREE_TRIAL = "free_trial"
TIER_SELF_FILE = "self_file"
TIER_AUTO_FILE = "auto_file"  # v1.1, not yet sold

TIER_ORDER = {
    TIER_FREE_TRIAL: 0,
    TIER_SELF_FILE: 1,
    TIER_AUTO_FILE: 2,
}

SELF_FILE_PRICE_LKR = 2500
SELF_FILE_PRICE_CENTS = SELF_FILE_PRICE_LKR * 100  # Stripe wants smallest unit


# --------------------------------------------------------------------------- #
# Tax-year boundary helpers.
# --------------------------------------------------------------------------- #

def current_sl_tax_year(today: Optional[date] = None) -> str:
    """Return the current Sri Lankan tax year as ``YYYY/YY`` (e.g. ``"2025/26"``).

    The SL tax year runs 1 Apr -> 31 Mar. So Aug 2025 is YA 2025/26, but
    Feb 2026 is also YA 2025/26 — it doesn't tick over until 1 Apr 2026.

    Pure function; deterministic; no side-effects. Defaults to ``date.today()``.
    """
    today = today or date.today()
    if today.month >= 4:
        start_year = today.year
    else:
        start_year = today.year - 1
    end_year = start_year + 1
    return f"{start_year}/{str(end_year)[-2:]}"


def expires_at_for_tax_year(tax_year: Optional[str] = None,
                             today: Optional[date] = None) -> datetime:
    """Return the expiry instant for a Self-File subscription: 31 Mar 23:59:59
    of TY+1, treated naively (consistent with the rest of FIESTA which uses
    naive UTC for ``datetime.utcnow()`` storage).
    """
    tax_year = tax_year or current_sl_tax_year(today=today)
    # tax_year is "YYYY/YY" — parse the leading 4-digit year
    start_year = int(tax_year.split("/")[0])
    end_year = start_year + 1
    return datetime(end_year, 3, 31, 23, 59, 59)


# --------------------------------------------------------------------------- #
# ORM bindings — done in register_models() so app.py / db must be importable
# at call time, not at module import time. Mirrors the pattern used by
# engagement_models.py + remittance_models.py.
# --------------------------------------------------------------------------- #

Subscription = None  # type: ignore[assignment]
PaywallEvent = None  # type: ignore[assignment]
StripeEvent = None   # type: ignore[assignment]


_registered = False


def register_models():
    """Define Subscription + PaywallEvent + StripeEvent against the live ``db``.

    Idempotent. Safe to call multiple times. Returns the three classes.
    """
    global Subscription, PaywallEvent, StripeEvent, _registered
    if _registered:
        return Subscription, PaywallEvent, StripeEvent

    from app import db

    class _Subscription(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "paywall_subscription"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"),
                            nullable=False, index=True)
        tier = db.Column(db.String(32), nullable=False, default=TIER_SELF_FILE)
        tax_year = db.Column(db.String(8), nullable=False)  # e.g. "2025/26"
        purchased_at = db.Column(db.DateTime, nullable=False,
                                 default=datetime.utcnow)
        expires_at = db.Column(db.DateTime, nullable=False)
        stripe_payment_intent_id = db.Column(db.String(255), nullable=True,
                                             unique=True, index=True)
        stripe_session_id = db.Column(db.String(255), nullable=True, index=True)
        amount_paid_lkr = db.Column(db.Integer, nullable=True)  # whole rupees
        # 'active' | 'expired' | 'refunded'. Expired is computed in code on
        # read; this column lets the refund webhook flip the row authoritatively.
        status = db.Column(db.String(16), nullable=False, default="active")
        refunded_at = db.Column(db.DateTime, nullable=True)
        # Optional linkage to the paywall_event row that triggered the
        # purchase — enables PM-grade funnel analytics (which screen drove
        # this conversion?).
        triggering_paywall_event_id = db.Column(
            db.Integer,
            db.ForeignKey("paywall_event.id"),
            nullable=True,
        )

        # ---------------- Tier D1 / C1: auto-renew columns ----------------
        # Populated when the row represents a recurring Stripe Subscription
        # rather than a one-time payment_intent purchase. Both billing models
        # coexist; see migrations/add_subscription_autorenew.py.
        #
        # auto_renew:           True iff this row is backed by a Stripe
        #                       Subscription that will renew automatically.
        # stripe_subscription_id: Stripe `sub_...` id. Unique index for the
        #                       webhook lookup path.
        # stripe_customer_id:   Stripe `cus_...` id. Required for the
        #                       customer billing portal redirect.
        # current_period_end:   Stripe-authoritative end of the current paid
        #                       period. Updated by invoice.paid /
        #                       customer.subscription.updated. We mirror this
        #                       into ``expires_at`` so the existing
        #                       ``is_active`` helper Just Works.
        # cancel_at_period_end: True if the user has scheduled cancellation
        #                       via the billing portal. Access remains until
        #                       current_period_end; then customer.subscription.
        #                       deleted flips status='cancelled'.
        auto_renew = db.Column(db.Boolean, nullable=False, default=False)
        stripe_subscription_id = db.Column(
            db.String(255), nullable=True, unique=True, index=True,
        )
        stripe_customer_id = db.Column(
            db.String(255), nullable=True, index=True,
        )
        current_period_end = db.Column(db.DateTime, nullable=True)
        cancel_at_period_end = db.Column(
            db.Boolean, nullable=False, default=False,
        )

        def __repr__(self):  # pragma: no cover
            return (f"<Subscription id={self.id} user={self.user_id} "
                    f"tier={self.tier} status={self.status} "
                    f"expires={self.expires_at.isoformat() if self.expires_at else '?'}>")

        @property
        def is_active(self) -> bool:
            """Return True iff status='active' AND now < expires_at.

            We DO NOT mutate the row on read — the schedulers / refund hook
            are the only authoritative writers. Computed property is fine.
            """
            if self.status != "active":
                return False
            return datetime.utcnow() < self.expires_at

    class _PaywallEvent(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "paywall_event"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"),
                            nullable=True, index=True)
        screen_id = db.Column(db.String(8), nullable=False, index=True)
        action_attempted = db.Column(db.String(255), nullable=True)
        required_tier = db.Column(db.String(32), nullable=False,
                                  default=TIER_SELF_FILE)
        fired_at = db.Column(db.DateTime, nullable=False,
                             default=datetime.utcnow, index=True)
        converted_at = db.Column(db.DateTime, nullable=True)
        conversion_revenue_lkr = db.Column(db.Integer, nullable=True)
        # Best-effort instrumentation context
        request_path = db.Column(db.String(512), nullable=True)
        user_agent = db.Column(db.String(512), nullable=True)
        was_ajax = db.Column(db.Boolean, nullable=False, default=False)

        def __repr__(self):  # pragma: no cover
            return (f"<PaywallEvent id={self.id} user={self.user_id} "
                    f"screen={self.screen_id} converted={self.converted_at}>")

    class _StripeEvent(db.Model):  # type: ignore[misc, valid-type]
        """Idempotency tombstone for Stripe webhook delivery."""
        __tablename__ = "paywall_stripe_event"

        id = db.Column(db.Integer, primary_key=True)
        stripe_event_id = db.Column(db.String(255), nullable=False,
                                    unique=True, index=True)
        event_type = db.Column(db.String(128), nullable=False)
        received_at = db.Column(db.DateTime, nullable=False,
                                default=datetime.utcnow)
        handled = db.Column(db.Boolean, nullable=False, default=False)
        handler_error = db.Column(db.String(500), nullable=True)

        def __repr__(self):  # pragma: no cover
            return (f"<StripeEvent {self.stripe_event_id} "
                    f"type={self.event_type} handled={self.handled}>")

    Subscription = _Subscription
    PaywallEvent = _PaywallEvent
    StripeEvent = _StripeEvent
    _registered = True
    log.info("fiesta.paywall models registered "
             "(paywall_subscription, paywall_event, paywall_stripe_event)")

    return Subscription, PaywallEvent, StripeEvent


__all__ = [
    "Subscription",
    "PaywallEvent",
    "StripeEvent",
    "register_models",
    "TIER_FREE_TRIAL",
    "TIER_SELF_FILE",
    "TIER_AUTO_FILE",
    "TIER_ORDER",
    "SELF_FILE_PRICE_LKR",
    "SELF_FILE_PRICE_CENTS",
    "current_sl_tax_year",
    "expires_at_for_tax_year",
]
