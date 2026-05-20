"""
fiesta.consultant.models — Booking ORM table (X4, Wave 6).

One row per paid consultant booking. Tied to a Stripe payment_intent for
audit + dedup; ``status`` is the lifecycle marker.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


CONSULTANT_PRICE_LKR = 5000
CONSULTANT_PRICE_CENTS = CONSULTANT_PRICE_LKR * 100  # Stripe wants smallest unit
CONSULTANT_SESSION_LENGTH_MIN = 30

# Google Calendar Appointment Schedule URL (M8 simplification — no Google
# Calendar API integration FIESTA-side; we hand the user off and Google
# issues the Meet link + sends invites).
CONSULTANT_CALENDAR_URL = "https://calendar.app.google/upp97vgtE7oYVdzn9"


Booking = None  # type: ignore[assignment]
_registered = False


def register_models():
    """Define the ``Booking`` model against the live ``db``.

    Idempotent. Returns the class.
    """
    global Booking, _registered
    if _registered:
        return Booking

    from app import db

    class _Booking(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "consultant_booking"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("user.id"),
                            nullable=False, index=True)
        stripe_payment_intent_id = db.Column(db.String(255), nullable=True,
                                              unique=True, index=True)
        stripe_session_id = db.Column(db.String(255), nullable=True, index=True)
        amount_paid_lkr = db.Column(db.Integer, nullable=True)
        purchased_at = db.Column(db.DateTime, nullable=False,
                                  default=datetime.utcnow)
        # 'paid_awaiting_redirect' | 'paid_redirected' | 'cancelled' | 'refunded'
        status = db.Column(db.String(32), nullable=False,
                            default="paid_awaiting_redirect")
        # Timestamp of when the prep-brief SendGrid email was confirmed sent.
        # NULL when not yet sent (the sweeper can retry).
        prep_brief_sent_at = db.Column(db.DateTime, nullable=True)
        prep_brief_error = db.Column(db.String(500), nullable=True)
        # Calendar URL the customer was redirected to. Cached for audit.
        calendar_redirect_url = db.Column(db.String(512), nullable=True)
        refunded_at = db.Column(db.DateTime, nullable=True)

        def __repr__(self):  # pragma: no cover
            return (f"<Booking id={self.id} user={self.user_id} "
                    f"status={self.status} "
                    f"purchased={self.purchased_at.isoformat() if self.purchased_at else '?'}>")

    Booking = _Booking
    _registered = True
    log.info("fiesta.consultant.models registered (consultant_booking)")
    return Booking


__all__ = [
    "Booking",
    "register_models",
    "CONSULTANT_PRICE_LKR",
    "CONSULTANT_PRICE_CENTS",
    "CONSULTANT_SESSION_LENGTH_MIN",
    "CONSULTANT_CALENDAR_URL",
]
