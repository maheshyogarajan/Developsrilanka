"""
referral_models.py - Tier D4 / A3: One-sided referral loop ORM tables.

Two tables on top of the existing SQLAlchemy ``db`` defined in app.py.

* ``ReferralCode`` - one row per existing paid user. Each user gets ONE code
  (enforced by unique user_id index). The 8-char hex code is shared by the
  referrer as ``/r/<code>``. Carries usage caps + expiry so a leaked code
  doesn't burn the company budget forever.

* ``ReferralRedemption`` - one row per new user who signs up with a referral
  cookie present. Created at signup. Flips to ``paid_at`` when the referee's
  first Stripe invoice is paid. Flips to ``referrer_credit_applied_at`` once
  the Stripe coupon is attached to the referrer's customer.

Scope cap (council-binding)
---------------------------
ONE-SIDED only. The REFERRER gets a 20%-off coupon on their NEXT invoice.
The REFEREE pays full price. No two-sided sweetener. No leaderboard. No
recurring discount.

Migration policy
----------------
We use ``db.create_all()`` at app boot (main.py) - same convention the rest
of the codebase follows (engagement_models, remittance_models, paywall.models).
A standalone migrations/add_referrals.py is shipped for the
``additive-DDL-on-prod`` path used by ``_ensure_additive_schema()``.

ORM bindings are deferred to ``register_models()`` so app.py / db must be
importable at call time, not at module import time. Mirrors the pattern used
by fiesta.paywall.models + engagement_models.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants (council-binding scope cap).
# --------------------------------------------------------------------------- #

REFERRAL_DISCOUNT_PERCENT = 20         # 20% off next invoice
REFERRAL_COUPON_DURATION = "once"      # NOT recurring
DEFAULT_MAX_USES = 100                 # cap per code
DEFAULT_EXPIRY_DAYS = 365              # 1 year
COOKIE_NAME = "referral_code"
COOKIE_MAX_AGE_SECONDS = 30 * 86400    # 30 days

# Stripe coupon naming: one coupon per redemption keeps the audit trail simple
# (we can revoke a single referral without disturbing others).
COUPON_NAME_PREFIX = "FIESTA_REFERRAL_REWARD"


# --------------------------------------------------------------------------- #
# Code generation - 8-char hex (32 bits, ~4.3B values).
# --------------------------------------------------------------------------- #

def generate_code() -> str:
    """Return a fresh 8-char hex referral code. Caller is responsible for
    uniqueness retry on the (vanishingly small) collision case."""
    return secrets.token_hex(4)  # 8 hex chars


# --------------------------------------------------------------------------- #
# ORM bindings.
# --------------------------------------------------------------------------- #

ReferralCode = None        # type: ignore[assignment]
ReferralRedemption = None  # type: ignore[assignment]

_registered = False


def register_models() -> Tuple[type, type]:
    """Define ReferralCode + ReferralRedemption against the live ``db``.

    Idempotent. Safe to call multiple times. Returns the two classes.
    """
    global ReferralCode, ReferralRedemption, _registered
    if _registered:
        return ReferralCode, ReferralRedemption

    from app import db

    class _ReferralCode(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "referral_code"

        id = db.Column(db.Integer, primary_key=True)
        # One code per user. Enforced unique so /api/referrals/generate is
        # idempotent without a SELECT-then-INSERT race.
        user_id = db.Column(
            db.Integer,
            db.ForeignKey("user.id"),
            nullable=False,
            unique=True,
            index=True,
        )
        code = db.Column(
            db.String(16),  # room for the 8-char hex + safety margin
            nullable=False,
            unique=True,
            index=True,
        )
        created_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )
        expires_at = db.Column(db.DateTime, nullable=True)
        max_uses = db.Column(
            db.Integer, nullable=False, default=DEFAULT_MAX_USES,
        )
        uses_count = db.Column(
            db.Integer, nullable=False, default=0,
        )
        is_active = db.Column(
            db.Boolean, nullable=False, default=True,
        )

        def __repr__(self):  # pragma: no cover
            return (
                f"<ReferralCode id={self.id} user={self.user_id} "
                f"code={self.code} uses={self.uses_count}/{self.max_uses} "
                f"active={self.is_active}>"
            )

        @property
        def is_redeemable(self) -> bool:
            """True iff active + not expired + not capped."""
            if not self.is_active:
                return False
            if self.expires_at is not None and datetime.utcnow() >= self.expires_at:
                return False
            if self.uses_count >= self.max_uses:
                return False
            return True

    class _ReferralRedemption(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "referral_redemption"

        id = db.Column(db.Integer, primary_key=True)
        code_id = db.Column(
            db.Integer,
            db.ForeignKey("referral_code.id"),
            nullable=False,
            index=True,
        )
        # The new user who signed up via the link.
        referee_user_id = db.Column(
            db.Integer,
            db.ForeignKey("user.id"),
            nullable=False,
            unique=True,    # a user can only ever be referred once
            index=True,
        )
        # Stripe subscription id (sub_...) for the referee's first paid
        # subscription. NULL until invoice.paid lands.
        referee_subscription_id = db.Column(
            db.String(255), nullable=True, index=True,
        )
        redeemed_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )
        paid_at = db.Column(db.DateTime, nullable=True)
        referrer_credit_applied_at = db.Column(db.DateTime, nullable=True)
        # Audit: the Stripe coupon id we attached to the referrer's customer.
        # Useful for revoke + reconciliation. NULL until credit application.
        referrer_coupon_id = db.Column(
            db.String(255), nullable=True,
        )

        def __repr__(self):  # pragma: no cover
            return (
                f"<ReferralRedemption id={self.id} code={self.code_id} "
                f"referee={self.referee_user_id} paid={self.paid_at} "
                f"credited={self.referrer_credit_applied_at}>"
            )

    ReferralCode = _ReferralCode
    ReferralRedemption = _ReferralRedemption
    _registered = True
    log.info(
        "referral_models registered (referral_code, referral_redemption)"
    )
    return ReferralCode, ReferralRedemption


# --------------------------------------------------------------------------- #
# Helpers used by routes + the Stripe webhook hook.
# --------------------------------------------------------------------------- #

def get_or_create_code_for_user(user_id: int) -> Optional[object]:
    """Return the user's ReferralCode row, creating one on first call.

    Idempotent. The unique-on-user_id index in the DB schema is the
    authoritative race-protection; the SELECT-first path is just to avoid
    one INSERT per call.
    """
    if user_id is None:
        return None
    if ReferralCode is None:
        log.error("ReferralCode model not registered - call register_models()")
        return None

    from app import db

    existing = ReferralCode.query.filter_by(user_id=user_id).first()
    if existing is not None:
        return existing

    # Generate a code with a 5-retry loop on the (vanishingly small)
    # hex-collision case. After 5 retries, give up and surface to caller.
    for _attempt in range(5):
        candidate = generate_code()
        if ReferralCode.query.filter_by(code=candidate).first() is None:
            row = ReferralCode(
                user_id=user_id,
                code=candidate,
                expires_at=datetime.utcnow() + timedelta(days=DEFAULT_EXPIRY_DAYS),
                max_uses=DEFAULT_MAX_USES,
                uses_count=0,
                is_active=True,
            )
            try:
                db.session.add(row)
                db.session.commit()
                return row
            except Exception as exc:
                db.session.rollback()
                # IntegrityError on the user_id unique index => race;
                # another request beat us. Re-fetch + return that.
                existing = ReferralCode.query.filter_by(user_id=user_id).first()
                if existing is not None:
                    return existing
                log.warning(
                    "get_or_create_code_for_user: insert failed user=%s: %s",
                    user_id, exc,
                )
                continue
    log.error(
        "get_or_create_code_for_user: exhausted 5 retries for user=%s",
        user_id,
    )
    return None


def lookup_redeemable_code(code: str) -> Optional[object]:
    """Look up a ReferralCode by code value. Returns None if not found or
    not redeemable (expired / capped / inactive)."""
    if not code or ReferralCode is None:
        return None
    row = ReferralCode.query.filter_by(code=code).first()
    if row is None:
        return None
    if not row.is_redeemable:
        return None
    return row


def record_signup_redemption(
    code_value: str, referee_user_id: int,
) -> Optional[object]:
    """Called from the signup flow when a new user signs up with the
    ``referral_code`` cookie present. Creates a ReferralRedemption row +
    bumps the code's uses_count. Idempotent on (referee_user_id) - if a
    redemption for this referee already exists, returns it without bumping.

    Returns the redemption row or None on failure (invalid code, capped,
    self-referral attempt).
    """
    if ReferralCode is None or ReferralRedemption is None:
        log.error("record_signup_redemption: models not registered")
        return None
    from app import db

    code_row = lookup_redeemable_code(code_value)
    if code_row is None:
        log.info(
            "record_signup_redemption: code=%s not redeemable - skip",
            (code_value or "")[:16],
        )
        return None

    # Self-referral guard: a user cannot redeem their own code.
    if code_row.user_id == referee_user_id:
        log.info(
            "record_signup_redemption: self-referral by user=%s blocked",
            referee_user_id,
        )
        return None

    existing = (
        ReferralRedemption.query
        .filter_by(referee_user_id=referee_user_id)
        .first()
    )
    if existing is not None:
        return existing

    row = ReferralRedemption(
        code_id=code_row.id,
        referee_user_id=referee_user_id,
        redeemed_at=datetime.utcnow(),
    )
    try:
        db.session.add(row)
        # Bump uses_count atomically. We accept the small overshoot risk
        # (concurrent signups can race past max_uses by 1-2). This is
        # cheaper than row-locking + the consequence is "1 extra free
        # referral", not "credit applied to wrong customer".
        code_row.uses_count = (code_row.uses_count or 0) + 1
        db.session.commit()
        log.info(
            "record_signup_redemption: referee=%s redeemed code=%s (uses=%s)",
            referee_user_id, code_row.code, code_row.uses_count,
        )
        return row
    except Exception as exc:
        db.session.rollback()
        log.warning(
            "record_signup_redemption: insert failed referee=%s: %s",
            referee_user_id, exc,
        )
        # Recover from the unique-on-referee race.
        existing = (
            ReferralRedemption.query
            .filter_by(referee_user_id=referee_user_id)
            .first()
        )
        return existing


def find_pending_redemption_for_referee(
    referee_user_id: int,
) -> Optional[object]:
    """Return the ReferralRedemption for this referee where paid_at is
    still NULL. Used by the Stripe invoice.paid hook to detect "this is
    the first payment from a referred user"."""
    if ReferralRedemption is None or referee_user_id is None:
        return None
    return (
        ReferralRedemption.query
        .filter_by(referee_user_id=referee_user_id, paid_at=None)
        .first()
    )


__all__ = [
    "ReferralCode",
    "ReferralRedemption",
    "register_models",
    "generate_code",
    "get_or_create_code_for_user",
    "lookup_redeemable_code",
    "record_signup_redemption",
    "find_pending_redemption_for_referee",
    "REFERRAL_DISCOUNT_PERCENT",
    "REFERRAL_COUPON_DURATION",
    "DEFAULT_MAX_USES",
    "DEFAULT_EXPIRY_DAYS",
    "COOKIE_NAME",
    "COOKIE_MAX_AGE_SECONDS",
    "COUPON_NAME_PREFIX",
]
