"""
dunning_sequence.py — Tier D3 / C5: Failed-payment recovery loop.

Wave 1 #C1 (webhooks/stripe_subscription.py) already flips
Subscription.status='dunning' on invoice.payment_failed and emits an analytics
event. C5 is the RECOVERY LOOP layered on top:

  1. Persist a per-failure Dunning row (audit + banner state).
  2. Telegram alert to CEO via ops_alerts.send_alert.
  3. Compose a customer-facing email (subject + body). Actual SES/Mailgun
     wire-up is OUT OF SCOPE — surfaced as a TODO at the bottom of this file.
  4. Banner gate: should_show_banner(user_id) returns True iff the user has
     at least one open (state='pending') Dunning row.

Scope cap (binding)
-------------------
  * NO email sending. compose_failed_payment_email returns subject+body only.
  * NO Stripe smart retries — Stripe handles that natively.
  * NO grace period override — subscription state stays Stripe-authoritative.
  * Banner shows on EVERY authenticated page until invoice.paid resolves the
    Dunning row (no dismiss).

Model
-----
Dunning ORM defined in migrations/add_dunning.py and registered via
register_dunning_model(). One row per (subscription, stripe_invoice_id) —
the same invoice can fail multiple times (Stripe retries 4x by default);
we update attempt_count + last_failed_at on each retry.

State enum: pending | recovered | abandoned.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# ORM binding — late-bound, mirrors fiesta.paywall.models pattern.
# --------------------------------------------------------------------------- #

Dunning = None  # type: ignore[assignment]
_registered = False

STATE_PENDING = "pending"
STATE_RECOVERED = "recovered"
STATE_ABANDONED = "abandoned"


def register_dunning_model():
    """Define Dunning ORM against the live ``db``. Idempotent."""
    global Dunning, _registered
    if _registered:
        return Dunning

    from app import db

    class _Dunning(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "paywall_dunning"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"),
            nullable=False, index=True,
        )
        subscription_id = db.Column(
            db.Integer, db.ForeignKey("paywall_subscription.id"),
            nullable=False, index=True,
        )
        stripe_invoice_id = db.Column(
            db.String(255), nullable=False, index=True,
        )
        attempt_count = db.Column(db.Integer, nullable=False, default=1)
        first_failed_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )
        last_failed_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )
        next_retry_at = db.Column(db.DateTime, nullable=True)
        # 'pending' | 'recovered' | 'abandoned'
        state = db.Column(
            db.String(16), nullable=False, default=STATE_PENDING, index=True,
        )
        resolved_at = db.Column(db.DateTime, nullable=True)

        __table_args__ = (
            db.UniqueConstraint(
                "subscription_id", "stripe_invoice_id",
                name="uq_paywall_dunning_sub_invoice",
            ),
        )

        def __repr__(self):  # pragma: no cover
            return (
                f"<Dunning id={self.id} user={self.user_id} "
                f"sub={self.subscription_id} state={self.state} "
                f"attempts={self.attempt_count}>"
            )

    Dunning = _Dunning
    _registered = True
    log.info("dunning_sequence: Dunning model registered (paywall_dunning)")
    return Dunning


# --------------------------------------------------------------------------- #
# Public API: webhook integration.
# --------------------------------------------------------------------------- #

def record_failed_payment(
    user_id: int,
    subscription_id: int,
    stripe_invoice_id: str,
    attempt_count: int,
    next_retry_at: Optional[datetime] = None,
) -> Optional[int]:
    """Persist a Dunning row + fire Telegram alert.

    Idempotent on (subscription_id, stripe_invoice_id): if a row already
    exists for the same invoice, update attempt_count + last_failed_at +
    next_retry_at instead of inserting a duplicate.

    Returns the Dunning row id on success, None on failure (never raises;
    failures logged + alerted but don't break the webhook handler).
    """
    if not user_id or not subscription_id or not stripe_invoice_id:
        log.warning(
            "record_failed_payment: missing required arg "
            "(user=%s, sub=%s, invoice=%s)",
            user_id, subscription_id, stripe_invoice_id,
        )
        return None

    try:
        from app import db
        if Dunning is None:
            register_dunning_model()
        if Dunning is None:  # registration failed
            log.error("record_failed_payment: Dunning model unavailable")
            return None

        now = datetime.utcnow()
        row = (
            Dunning.query
            .filter_by(
                subscription_id=subscription_id,
                stripe_invoice_id=stripe_invoice_id,
            )
            .first()
        )
        if row is None:
            row = Dunning(
                user_id=user_id,
                subscription_id=subscription_id,
                stripe_invoice_id=stripe_invoice_id,
                attempt_count=attempt_count or 1,
                first_failed_at=now,
                last_failed_at=now,
                next_retry_at=next_retry_at,
                state=STATE_PENDING,
            )
            db.session.add(row)
        else:
            # Idempotent retry update — same invoice failing again.
            row.attempt_count = max(row.attempt_count, attempt_count or 1)
            row.last_failed_at = now
            if next_retry_at is not None:
                row.next_retry_at = next_retry_at
            # If a prior failure was somehow marked recovered/abandoned and
            # Stripe is re-firing, reopen as pending — Stripe is authoritative.
            if row.state != STATE_PENDING:
                row.state = STATE_PENDING
                row.resolved_at = None

        db.session.flush()
        row_id = row.id
        db.session.commit()
    except Exception as exc:
        log.exception(
            "record_failed_payment: DB write failed for user=%s sub=%s: %s",
            user_id, subscription_id, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None

    # Telegram alert — best-effort, never breaks the caller.
    try:
        from ops_alerts import send_alert
        send_alert(
            severity="MEDIUM",
            title="Payment failed",
            body=(
                f"User {user_id}, subscription {subscription_id}, "
                f"attempt {attempt_count}"
            ),
            data={
                "user_id": user_id,
                "subscription_id": subscription_id,
                "stripe_invoice_id": stripe_invoice_id,
                "attempt_count": attempt_count,
                "next_retry_at": (
                    next_retry_at.isoformat() if next_retry_at else None
                ),
                "dunning_row_id": row_id,
            },
        )
    except Exception as exc:
        log.warning("record_failed_payment: ops_alerts send failed: %s", exc)

    return row_id


def mark_invoice_paid(stripe_invoice_id: str) -> int:
    """Mark every open Dunning row for this invoice as recovered.

    Called from invoice.paid webhook. Returns the count of rows updated.
    Idempotent: calling twice is a no-op the second time.
    """
    if not stripe_invoice_id:
        return 0

    try:
        from app import db
        if Dunning is None:
            register_dunning_model()
        if Dunning is None:
            return 0

        rows = (
            Dunning.query
            .filter_by(
                stripe_invoice_id=stripe_invoice_id,
                state=STATE_PENDING,
            )
            .all()
        )
        if not rows:
            return 0

        now = datetime.utcnow()
        for r in rows:
            r.state = STATE_RECOVERED
            r.resolved_at = now
        db.session.commit()
        return len(rows)
    except Exception as exc:
        log.exception(
            "mark_invoice_paid: DB update failed for invoice=%s: %s",
            stripe_invoice_id, exc,
        )
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return 0


# --------------------------------------------------------------------------- #
# Email composition (compose-only; no SES/Mailgun wiring yet — see TODO).
# --------------------------------------------------------------------------- #

def compose_failed_payment_email(user, invoice: dict) -> dict:
    """Build subject + plain-text body for a payment-failed customer email.

    Args:
        user:    User ORM row (must have .email, .name).
        invoice: Stripe invoice dict (keys used: id, amount_due, currency,
                 attempt_count, next_payment_attempt).

    Returns:
        {"to": str, "subject": str, "body": str}

    Doesn't send. The dunning row + Telegram alert are wired; actual customer
    delivery is the SES/Mailgun follow-up task (see TODO at bottom of file).
    """
    name = (getattr(user, "name", None) or "").strip() or "there"
    email = getattr(user, "email", "") or ""

    amount_due = invoice.get("amount_due") or 0
    currency = (invoice.get("currency") or "lkr").upper()
    attempt_count = invoice.get("attempt_count") or 1
    next_attempt_unix = invoice.get("next_payment_attempt")

    # Stripe amounts are in the smallest currency unit (cents / paise).
    # For LKR Stripe uses the full rupee as the unit (zero-decimal). Be
    # defensive: format as a 2-dp string if the value looks subunit-sized.
    if currency in ("LKR", "JPY", "KRW", "VND"):
        amount_display = f"{currency} {amount_due:,}"
    else:
        amount_display = f"{currency} {amount_due / 100:,.2f}"

    next_attempt_str = "We will retry the charge automatically."
    if next_attempt_unix:
        try:
            dt = datetime.utcfromtimestamp(next_attempt_unix)
            next_attempt_str = (
                f"We will retry the charge automatically on "
                f"{dt.strftime('%d %b %Y')} (UTC)."
            )
        except Exception:
            pass

    subject = (
        f"Action needed: we couldn't charge your card "
        f"(attempt {attempt_count})"
    )

    body = (
        f"Hi {name},\n\n"
        f"We tried to renew your FIESTA subscription and the charge for "
        f"{amount_display} was declined by your card issuer.\n\n"
        f"{next_attempt_str} Until the payment succeeds, your subscription "
        f"will continue but a yellow banner will appear at the top of every "
        f"page in your dashboard.\n\n"
        f"What to do:\n"
        f"  1. Open https://app.fiesta.tax/billing and update your card.\n"
        f"  2. Or contact your bank if you believe the decline was an error.\n\n"
        f"If you've already updated your card, you can ignore this email — "
        f"the next retry will pick up the new details automatically.\n\n"
        f"Thank you,\nThe FIESTA team\n"
    )

    return {"to": email, "subject": subject, "body": body}


# --------------------------------------------------------------------------- #
# Banner gate.
# --------------------------------------------------------------------------- #

def should_show_banner(user_id: Optional[int]) -> bool:
    """True iff the user has at least one open (pending) Dunning row.

    Cheap: single-column COUNT against the index. Safe for every-request
    use from a Jinja context processor. Returns False on any error so the
    banner never blocks page render.
    """
    if not user_id:
        return False
    try:
        if Dunning is None:
            register_dunning_model()
        if Dunning is None:
            return False
        return (
            Dunning.query
            .filter_by(user_id=user_id, state=STATE_PENDING)
            .first()
            is not None
        )
    except Exception as exc:
        log.debug("should_show_banner: query failed: %s", exc)
        return False


# --------------------------------------------------------------------------- #
# Flask context processor (call from main.py after app create).
# --------------------------------------------------------------------------- #

def register_context_processor(app) -> None:
    """Inject should_show_dunning_banner into every template render.

    Idempotent. Safe to call from main.py wire-time.
    """
    if getattr(app, "_dunning_ctx_registered", False):
        return

    @app.context_processor
    def _inject_dunning_banner():
        try:
            from flask_login import current_user
            if not current_user or not current_user.is_authenticated:
                return {"should_show_dunning_banner": False}
            return {
                "should_show_dunning_banner": should_show_banner(
                    getattr(current_user, "id", None)
                ),
            }
        except Exception:
            return {"should_show_dunning_banner": False}

    app._dunning_ctx_registered = True
    log.info("dunning_sequence: context processor registered")


__all__ = [
    "Dunning",
    "register_dunning_model",
    "register_context_processor",
    "record_failed_payment",
    "mark_invoice_paid",
    "compose_failed_payment_email",
    "should_show_banner",
    "STATE_PENDING",
    "STATE_RECOVERED",
    "STATE_ABANDONED",
]


# TODO(c5-followup): Wire compose_failed_payment_email() into the existing
# SendGrid / SES / Mailgun email path. Out of scope for this task — the
# Dunning row + Telegram alert + in-app banner are the end-to-end customer
# recovery loop for v1. Email delivery requires:
#   1. Choosing the provider (sendgrid_logger.py already imports SendGrid; reuse?).
#   2. A worker hook on Dunning row create (Celery task or post-flush listener).
#   3. Per-user delivery dedup so retries don't spam.
# File a separate task before turning email send on.
