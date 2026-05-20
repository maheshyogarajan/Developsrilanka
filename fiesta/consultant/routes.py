"""
fiesta.consultant.routes — X4 Consultant booking endpoints (Wave 6, 2026-05-21).

Routes:

  GET  /consultant/book           — booking landing page (price + CTA).
                                    Available to ALL signed-up tiers.
  POST /consultant/book/checkout  — create Stripe one-off Checkout Session
                                    (mode=payment, Rs 5,000 LKR), redirect
                                    to Stripe.
  GET  /consultant/book/success   — post-Stripe landing. Records the
                                    Booking row (idempotent via session_id)
                                    + fires the SendGrid prep brief + 303
                                    redirect to the Google Calendar
                                    appointment link.
  GET  /consultant/book/cancel    — Stripe cancel landing — back to /consultant/book.
  POST /webhooks/stripe/consultant — Stripe webhook (one-off bookings).
                                    Distinct from /webhooks/stripe/paywall —
                                    bookings don't mint a Subscription.

The Stripe path uses the same ``STRIPE_SECRET_KEY`` + webhook signing
secret as the X1 paywall. Reuse is by-design: one Stripe account, two
products (Self-File subscription + Consultant booking) distinguished by
``metadata.product``.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from flask import (
    Blueprint, Flask, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)
from flask_login import current_user, login_required

from events import emit as emit_analytics_event
from .models import (
    Booking, CONSULTANT_CALENDAR_URL,
    CONSULTANT_PRICE_CENTS, CONSULTANT_PRICE_LKR,
    CONSULTANT_SESSION_LENGTH_MIN, register_models,
)

log = logging.getLogger(__name__)


consultant_bp = Blueprint("consultant", __name__, url_prefix="")


# --------------------------------------------------------------------------- #
# Booking landing — visible to all signed-up tiers (no paywall gate).
# --------------------------------------------------------------------------- #

@consultant_bp.route("/consultant/book", methods=["GET"])
@login_required
def book_landing():
    """Booking CTA page. ``return_to`` is preserved across the Stripe round-trip."""
    return_to = (request.args.get("return_to") or "/dashboard").strip()
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/dashboard"

    emit_analytics_event(
        "consultant_book_landing_viewed",
        user_id=current_user.id,
        payload={"return_to": return_to,
                 "price_lkr": CONSULTANT_PRICE_LKR},
        source="route:consultant.book_landing",
    )

    return render_template(
        "consultant/book.html",
        price_lkr=CONSULTANT_PRICE_LKR,
        session_minutes=CONSULTANT_SESSION_LENGTH_MIN,
        return_to=return_to,
    )


# --------------------------------------------------------------------------- #
# Stripe one-off checkout.
# --------------------------------------------------------------------------- #

@consultant_bp.route("/consultant/book/checkout", methods=["POST"])
@login_required
def book_checkout():
    """Create Stripe Checkout Session (mode='payment') and redirect."""
    return_to = (request.form.get("return_to") or "/dashboard").strip()
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/dashboard"

    emit_analytics_event(
        "consultant_book_checkout_started",
        user_id=current_user.id,
        payload={"price_lkr": CONSULTANT_PRICE_LKR},
        source="route:consultant.book_checkout",
    )

    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("stripe SDK not installed; cannot start consultant checkout")
        flash("Booking is temporarily unavailable. Please try again later.",
              "warning")
        return redirect(url_for("consultant.book_landing"))

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        log.warning("STRIPE_SECRET_KEY not configured for consultant booking")
        flash("Booking is temporarily unavailable. Please try again later.",
              "warning")
        return redirect(url_for("consultant.book_landing"))

    stripe.api_key = secret_key

    try:
        success_url = url_for(
            "consultant.book_success",
            session_id="{CHECKOUT_SESSION_ID}",
            return_to=return_to,
            _external=True,
        )
        cancel_url = url_for(
            "consultant.book_cancel",
            return_to=return_to,
            _external=True,
        )
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "lkr",
                    "product_data": {
                        "name": "FIESTA — Consultant Booking",
                        "description": (f"One-off {CONSULTANT_SESSION_LENGTH_MIN}-min "
                                          "consultation via Google Meet."),
                    },
                    "unit_amount": CONSULTANT_PRICE_CENTS,
                },
                "quantity": 1,
            }],
            customer_email=getattr(current_user, "email", None),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(current_user.id),
                "product": "consultant_booking",
                "return_to": return_to,
            },
        )
    except Exception as exc:
        log.exception("consultant stripe checkout failed: %s", exc)
        flash("Couldn't start the booking checkout. Please try again, "
              "or email support.", "danger")
        return redirect(url_for("consultant.book_landing"))

    return redirect(session.url, code=303)


# --------------------------------------------------------------------------- #
# Stripe success landing — record + redirect to Google Calendar.
# --------------------------------------------------------------------------- #

@consultant_bp.route("/consultant/book/success", methods=["GET"])
@login_required
def book_success():
    """Post-Stripe-success handoff: create Booking row (idempotent via
    session_id), fire prep brief (best-effort), 303 to Google Calendar URL.
    """
    session_id = (request.args.get("session_id") or "").strip()
    return_to = (request.args.get("return_to") or "/dashboard").strip()

    register_models()
    from .models import Booking as _Booking  # post-register import
    from app import db

    booking = None
    if session_id:
        try:
            booking = _Booking.query.filter_by(
                stripe_session_id=session_id
            ).first()
        except Exception:
            booking = None

    if booking is None:
        # Pull authoritative payment_intent + amount from Stripe (the
        # webhook is the ledger; this view records best-effort so the
        # customer never sits looking at a blank page if the webhook is
        # delayed).
        amount_paid = None
        payment_intent_id = None
        try:
            import stripe  # type: ignore
            key = os.environ.get("STRIPE_SECRET_KEY")
            if key and session_id:
                stripe.api_key = key
                sess = stripe.checkout.Session.retrieve(session_id)
                amount_paid = int((sess.get("amount_total") or 0) / 100)
                payment_intent_id = sess.get("payment_intent")
        except Exception as exc:
            log.warning("consultant: stripe session retrieve failed (%s) — "
                         "creating booking without payment_intent", exc)

        try:
            booking = _Booking(
                user_id=current_user.id,
                stripe_session_id=session_id or None,
                stripe_payment_intent_id=payment_intent_id,
                amount_paid_lkr=amount_paid,
                purchased_at=datetime.utcnow(),
                status="paid_awaiting_redirect",
                calendar_redirect_url=CONSULTANT_CALENDAR_URL,
            )
            db.session.add(booking)
            db.session.commit()
        except Exception as exc:
            log.exception("consultant: Booking insert failed: %s", exc)
            db.session.rollback()
            booking = None

    # Best-effort prep-brief send. Wrapped in try so failure never blocks
    # the customer's Calendar handoff — the sweeper can retry from the
    # NULL prep_brief_sent_at flag.
    if booking is not None and booking.prep_brief_sent_at is None:
        try:
            sent_ok, send_error = _send_consultant_prep_brief(
                booking=booking, user=current_user
            )
            if sent_ok:
                booking.prep_brief_sent_at = datetime.utcnow()
                booking.status = "paid_redirected"
                db.session.commit()
            elif send_error:
                booking.prep_brief_error = send_error[:500]
                db.session.commit()
        except Exception as exc:
            log.warning("consultant: prep brief side-effect failed: %s", exc)
            db.session.rollback()

    emit_analytics_event(
        "consultant_book_success_redirect",
        user_id=current_user.id,
        payload={
            "session_id": session_id[:64],
            "booking_id": getattr(booking, "id", None),
            "amount_paid_lkr": getattr(booking, "amount_paid_lkr", None),
            "return_to": return_to,
        },
        source="route:consultant.book_success",
    )

    return redirect(CONSULTANT_CALENDAR_URL, code=303)


@consultant_bp.route("/consultant/book/cancel", methods=["GET"])
@login_required
def book_cancel():
    flash("Booking cancelled — no payment taken. You can try again any time.",
          "info")
    return redirect(url_for("consultant.book_landing"))


# --------------------------------------------------------------------------- #
# Stripe webhook for consultant bookings (idempotent).
# --------------------------------------------------------------------------- #

@consultant_bp.route("/webhooks/stripe/consultant", methods=["POST"])
def stripe_webhook():
    """One-off booking webhook. Handles ``checkout.session.completed`` (mint
    Booking row if not already created by /book/success) and ``charge.refunded``
    (mark booking refunded).
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe  # type: ignore
    except ImportError:
        return jsonify({"error": "stripe SDK not installed"}), 503

    # We reuse the paywall webhook secret env var so operators have one
    # place to swap when rotating. A dedicated STRIPE_CONSULTANT_WEBHOOK_SECRET
    # falls back to it.
    secret = (
        os.environ.get("STRIPE_CONSULTANT_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_PAYWALL_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_WEBHOOK_SECRET")
    )
    if not secret:
        return jsonify({"error": "webhook secret not configured"}), 503

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        return jsonify({"error": "invalid payload"}), 400
    except Exception:
        return jsonify({"error": "signature verification failed"}), 400

    event_id = stripe_event.get("id", "")
    event_type = stripe_event.get("type", "")

    # Idempotency tombstone — reuse the X1 paywall_stripe_event table.
    if _stripe_event_already_handled(event_id):
        return jsonify({"received": True, "duplicate": True}), 200

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(stripe_event)
        elif event_type == "charge.refunded":
            _handle_charge_refunded(stripe_event)
        else:
            _mark_stripe_event(event_id, event_type, handled=False)
            return jsonify({"received": True, "handled": False}), 200

        _mark_stripe_event(event_id, event_type, handled=True)
        return jsonify({"received": True, "handled": True}), 200
    except Exception as exc:
        log.exception("consultant webhook handler crashed: %s", exc)
        _mark_stripe_event(event_id, event_type, handled=False,
                            error=str(exc))
        return jsonify({"received": True, "handled": False,
                         "error": "handler_failed"}), 200


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #

def _stripe_event_already_handled(event_id: str) -> bool:
    if not event_id:
        return False
    try:
        from fiesta.paywall.models import StripeEvent
        if StripeEvent is None:
            return False
        return (StripeEvent.query
                .filter_by(stripe_event_id=event_id)
                .first()
                is not None)
    except Exception as exc:
        log.warning("consultant dedup check failed: %s", exc)
        return False


def _mark_stripe_event(event_id: str, event_type: str, handled: bool,
                        error: Optional[str] = None) -> None:
    if not event_id:
        return
    try:
        from app import db
        from fiesta.paywall.models import StripeEvent
        if StripeEvent is None:
            return
        row = StripeEvent.query.filter_by(stripe_event_id=event_id).first()
        if row is not None:
            row.handled = handled
            if error:
                row.handler_error = error[:500]
        else:
            row = StripeEvent(stripe_event_id=event_id, event_type=event_type,
                                handled=handled,
                                handler_error=(error or "")[:500] or None)
            db.session.add(row)
        db.session.commit()
    except Exception as exc:
        log.warning("consultant mark stripe_event failed: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _handle_checkout_completed(stripe_event: dict) -> None:
    """Mint a Booking row from the webhook (if /book/success didn't already)."""
    session = stripe_event.get("data", {}).get("object", {}) or {}
    metadata = session.get("metadata") or {}

    # Guard: only handle our own product. The same Stripe account also
    # carries X1 paywall events; we don't want to double-process them here.
    if metadata.get("product") != "consultant_booking":
        log.info("consultant webhook: ignoring product=%s",
                 metadata.get("product"))
        return

    user_id_str = metadata.get("user_id")
    try:
        user_id = int(user_id_str) if user_id_str else None
    except (TypeError, ValueError):
        user_id = None
    if user_id is None:
        log.warning("consultant webhook: completed with no user_id")
        return

    payment_intent_id = session.get("payment_intent") or ""
    session_id = session.get("id") or ""
    amount_total_cents = session.get("amount_total") or 0

    register_models()
    from .models import Booking as _Booking
    from app import db

    if payment_intent_id:
        existing = _Booking.query.filter_by(
            stripe_payment_intent_id=payment_intent_id
        ).first()
        if existing is not None:
            log.info("consultant webhook: booking already exists for "
                      "payment_intent=%s — no-op", payment_intent_id[:32])
            return

    booking = _Booking(
        user_id=user_id,
        stripe_session_id=session_id or None,
        stripe_payment_intent_id=payment_intent_id or None,
        amount_paid_lkr=(int(amount_total_cents / 100)
                          if amount_total_cents else None),
        purchased_at=datetime.utcnow(),
        status="paid_awaiting_redirect",
        calendar_redirect_url=CONSULTANT_CALENDAR_URL,
    )
    db.session.add(booking)
    db.session.commit()

    emit_analytics_event(
        "consultant_book_completed_webhook",
        user_id=user_id,
        payload={
            "booking_id": booking.id,
            "amount_paid_lkr": booking.amount_paid_lkr,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_session_id": session_id,
        },
        source="webhook:consultant.checkout_completed",
    )


def _handle_charge_refunded(stripe_event: dict) -> None:
    charge = stripe_event.get("data", {}).get("object", {}) or {}
    payment_intent_id = charge.get("payment_intent") or ""
    if not payment_intent_id:
        return
    register_models()
    from .models import Booking as _Booking
    from app import db
    booking = _Booking.query.filter_by(
        stripe_payment_intent_id=payment_intent_id
    ).first()
    if booking is None:
        return
    booking.status = "refunded"
    booking.refunded_at = datetime.utcnow()
    db.session.commit()
    emit_analytics_event(
        "consultant_booking_refunded",
        user_id=booking.user_id,
        payload={"booking_id": booking.id,
                  "stripe_payment_intent_id": payment_intent_id},
        source="webhook:consultant.charge_refunded",
    )


def _send_consultant_prep_brief(*, booking, user) -> tuple[bool, Optional[str]]:
    """Send the consultant prep brief via SendGrid.

    Returns ``(ok, error)``. SendGrid is best-effort — if the lib isn't
    importable or the env var is missing, we return (False, error_string)
    so the caller persists the error for sweeper retry.

    The prep brief goes to ``CONSULTANT_PREP_BRIEF_RECIPIENT`` env var
    (defaults to the FROM address). It includes the customer's email, the
    Booking id, and a link to the calendar to confirm the slot.
    """
    try:
        import sendgrid  # type: ignore
        from sendgrid.helpers.mail import Mail  # type: ignore
    except ImportError:
        return False, "sendgrid library not installed"

    sg_key = os.environ.get("SENDGRID_API_KEY") or ""
    if not sg_key:
        return False, "SENDGRID_API_KEY not configured"

    from_email = (os.environ.get("CONSULTANT_PREP_BRIEF_FROM")
                   or os.environ.get("SENDGRID_FROM_EMAIL")
                   or "info@smarter.tax")
    to_email = (os.environ.get("CONSULTANT_PREP_BRIEF_RECIPIENT")
                  or from_email)
    subject = f"FIESTA — New consultant booking #{booking.id}"
    body_text = (
        f"A FIESTA customer just booked a {CONSULTANT_SESSION_LENGTH_MIN}-min "
        f"consultation.\n\n"
        f"Customer email: {getattr(user, 'email', '(unknown)')}\n"
        f"Customer name:  {getattr(user, 'name', '(unknown)')}\n"
        f"Booking id:     {booking.id}\n"
        f"Stripe session: {booking.stripe_session_id or '(unknown)'}\n"
        f"Amount paid:    Rs {booking.amount_paid_lkr or '?'} LKR\n"
        f"Booked at:      {booking.purchased_at.isoformat() if booking.purchased_at else '?'} UTC\n\n"
        f"The customer is being redirected to:\n"
        f"  {CONSULTANT_CALENDAR_URL}\n\n"
        f"Google Calendar will issue the Meet link automatically once they "
        f"pick a slot. Please confirm the slot in your calendar within a "
        f"few hours.\n"
    )

    try:
        client = sendgrid.SendGridAPIClient(api_key=sg_key)
        mail = Mail(from_email=from_email, to_emails=to_email,
                     subject=subject, plain_text_content=body_text)
        resp = client.send(mail)
        if 200 <= resp.status_code < 300:
            return True, None
        return False, f"sendgrid status={resp.status_code}"
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------- #
# Registration helper.
# --------------------------------------------------------------------------- #

def register_routes(app: Flask) -> None:
    """Register the consultant blueprint + CSRF-exempt the webhook."""
    register_models()
    if "consultant" in app.blueprints:
        log.debug("consultant blueprint already registered — skipping.")
        return
    app.register_blueprint(consultant_bp)
    try:
        from app import csrf
        try:
            csrf.exempt(consultant_bp)
        except Exception:
            csrf.exempt(stripe_webhook)
    except Exception as exc:
        log.warning("Could not CSRF-exempt consultant webhook (%s); "
                    "POSTs to /webhooks/stripe/consultant may 400", exc)
    log.info("X4 consultant blueprint registered (/consultant/book, "
             "/consultant/book/checkout, /consultant/book/success, "
             "/consultant/book/cancel, /webhooks/stripe/consultant)")
