"""
webhooks/stripe_subscription.py — Tier D1 C1: Stripe subscription auto-renew
and the customer billing portal.

Scope (council-binding cap)
---------------------------
Happy path only. Single yearly tier. NO upgrade/downgrade flows. NO email
send wiring (Wave 3 C5 dunning ships email content). NO dunning logic depth
(we mark state; we don't act).

What this module does
---------------------
1. Webhook ``POST /webhooks/stripe/subscription`` — receives + verifies
   subscription-mode lifecycle events:

     * ``invoice.paid``                  -> subscription active, extend
                                            current_period_end +
                                            access_expiration_date.
     * ``invoice.payment_failed``        -> status='dunning' (stub for C5).
     * ``customer.subscription.updated`` -> mirror cancel_at_period_end,
                                            current_period_end.
     * ``customer.subscription.deleted`` -> status='cancelled'; access ends
                                            at current_period_end.

   Idempotent: re-uses the existing ``paywall_stripe_event`` tombstone table
   (fiesta.paywall.models.StripeEvent) so we don't double-process Stripe
   re-deliveries.

2. Billing portal redirect ``GET /billing`` — creates a Stripe Customer
   Portal Session for ``current_user`` and 302s to it. From the portal the
   user can update their card, cancel, or resume.

Why a SEPARATE webhook endpoint (vs extending the existing one)?
---------------------------------------------------------------
The existing handlers at ``stripe_routes`` and
``fiesta.paywall.pricing_screen`` were built for ``mode='payment'`` (one-time
Checkout). Stripe lets you wire each webhook endpoint to a DIFFERENT signing
secret in the dashboard, which is operationally valuable: a misconfigured
secret only takes down its own surface. Keeping subscription events on
a dedicated endpoint also means the existing one-time tests stay green
without retro-changes.

CSRF + signature verification
-----------------------------
Identical pattern to fiesta.paywall.pricing_screen: the webhook is
CSRF-exempted at blueprint registration, and the body is signature-verified
using ``stripe.Webhook.construct_event`` against
``STRIPE_SUBSCRIPTION_WEBHOOK_SECRET`` (falls back to
``STRIPE_PAYWALL_WEBHOOK_SECRET`` then ``STRIPE_WEBHOOK_SECRET``).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from flask import (
    Blueprint, request, redirect, url_for, jsonify, flash, current_app,
    render_template,
)
from flask_login import login_required, current_user

from events import emit as emit_analytics_event

log = logging.getLogger(__name__)


# Blueprint mounted under no prefix so URLs are stable: /webhooks/stripe/subscription
# and /billing. Tests rely on the exact paths.
subscription_bp = Blueprint("stripe_subscription", __name__, url_prefix="")


# --------------------------------------------------------------------------- #
# Webhook secret resolution.
# --------------------------------------------------------------------------- #

def _webhook_secret() -> Optional[str]:
    """Pick the signing secret. Subscription-specific first, then paywall,
    then the generic fallback. None if all three are unset."""
    return (
        os.environ.get("STRIPE_SUBSCRIPTION_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_PAYWALL_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_WEBHOOK_SECRET")
    )


# --------------------------------------------------------------------------- #
# Idempotency helpers — reuse paywall_stripe_event tombstone table.
# --------------------------------------------------------------------------- #

def _stripe_event_already_handled(event_id: str) -> bool:
    if not event_id:
        return False
    try:
        from fiesta.paywall.models import StripeEvent
        if StripeEvent is None:
            return False
        return (
            StripeEvent.query
            .filter_by(stripe_event_id=event_id)
            .first()
            is not None
        )
    except Exception as exc:
        log.warning("subscription-webhook dedup check failed: %s", exc)
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
            row = StripeEvent(
                stripe_event_id=event_id,
                event_type=event_type,
                handled=handled,
                handler_error=(error or "")[:500] or None,
            )
            db.session.add(row)
        db.session.commit()
    except Exception as exc:
        log.warning("subscription-webhook tombstone write failed: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Subscription row helpers.
# --------------------------------------------------------------------------- #

def _get_or_create_subscription(stripe_subscription_id: str,
                                stripe_customer_id: Optional[str],
                                user_id: Optional[int]):
    """Fetch a paywall_subscription row by stripe_subscription_id, or create
    one if absent (first invoice.paid for a brand-new subscription).

    Idempotent: subsequent calls with the same stripe_subscription_id return
    the existing row.
    """
    from app import db
    from fiesta.paywall.models import (
        Subscription, TIER_SELF_FILE, current_sl_tax_year,
        expires_at_for_tax_year,
    )
    if Subscription is None:
        log.error("Subscription model not registered — call register_models() first")
        return None

    row = (
        Subscription.query
        .filter_by(stripe_subscription_id=stripe_subscription_id)
        .first()
    )
    if row is not None:
        return row

    if user_id is None:
        log.warning(
            "subscription-webhook: cannot create row for stripe_sub=%s — no user_id",
            stripe_subscription_id[:32],
        )
        return None

    tax_year = current_sl_tax_year()
    row = Subscription(
        user_id=user_id,
        tier=TIER_SELF_FILE,
        tax_year=tax_year,
        purchased_at=datetime.utcnow(),
        # Provisional — overwritten by invoice.paid handler with the Stripe
        # current_period_end. Use 1-day expiry as a safety so a never-paid
        # row doesn't accidentally grant access.
        expires_at=datetime.utcnow(),
        status="active",
        auto_renew=True,
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        current_period_end=None,
        cancel_at_period_end=False,
    )
    db.session.add(row)
    db.session.flush()
    log.info(
        "subscription-webhook: created Subscription id=%s for stripe_sub=%s user=%s",
        row.id, stripe_subscription_id[:32], user_id,
    )
    return row


def _extract_user_id(stripe_object: dict) -> Optional[int]:
    """Pull user_id out of Stripe event metadata. Returns None if absent or
    not int-coercible."""
    metadata = stripe_object.get("metadata") or {}
    raw = metadata.get("user_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Per-event handlers.
# --------------------------------------------------------------------------- #

def _handle_invoice_paid(stripe_event: dict) -> None:
    """``invoice.paid`` — subscription is active for this paid period.

    Updates current_period_end + expires_at + access_expiration_date and
    flips status='active'. Creates the Subscription row on first paid
    invoice if it didn't exist yet.
    """
    invoice = stripe_event.get("data", {}).get("object", {}) or {}
    stripe_subscription_id = invoice.get("subscription") or ""
    stripe_customer_id = invoice.get("customer") or None
    if not stripe_subscription_id:
        log.info("invoice.paid: no subscription id on invoice — skipping")
        return

    # Stripe puts period_end on the invoice line items; for a yearly
    # subscription the invoice's `lines.data[0].period.end` is the
    # period-end timestamp. Fall back to invoice.period_end for safety.
    period_end_ts = None
    try:
        lines = invoice.get("lines", {}).get("data", [])
        if lines and isinstance(lines[0].get("period"), dict):
            period_end_ts = lines[0]["period"].get("end")
    except Exception:
        period_end_ts = None
    if period_end_ts is None:
        period_end_ts = invoice.get("period_end")

    user_id = _extract_user_id(invoice)
    # If invoice metadata doesn't carry user_id (Stripe only auto-propagates
    # metadata on Checkout, not invoices), try the subscription metadata
    # via a tracked row.
    if user_id is None:
        from fiesta.paywall.models import Subscription
        existing = (
            Subscription.query
            .filter_by(stripe_subscription_id=stripe_subscription_id)
            .first()
        )
        if existing is not None:
            user_id = existing.user_id

    row = _get_or_create_subscription(
        stripe_subscription_id=stripe_subscription_id,
        stripe_customer_id=stripe_customer_id,
        user_id=user_id,
    )
    if row is None:
        return

    if period_end_ts:
        new_end = datetime.utcfromtimestamp(period_end_ts)
        row.current_period_end = new_end
        # Mirror into expires_at so the existing is_active helper Just Works
        # without needing every consumer to branch on auto_renew.
        row.expires_at = new_end
    if stripe_customer_id and not row.stripe_customer_id:
        row.stripe_customer_id = stripe_customer_id
    row.status = "active"
    row.auto_renew = True

    # Also extend the User.access_expiration_date so the existing
    # access-gating code (which reads User, not Subscription) sees the
    # renewal. Best-effort; never raises.
    try:
        from models import User
        u = User.query.get(row.user_id) if row.user_id else None
        if u is not None and row.current_period_end is not None:
            if (u.access_expiration_date is None
                    or u.access_expiration_date < row.current_period_end):
                u.access_expiration_date = row.current_period_end
    except Exception as exc:
        log.debug("invoice.paid: User.access_expiration_date update skipped: %s", exc)

    from app import db
    db.session.commit()

    # Tier D6 / D8 — bust the per-user paywall tier cache so the freshly
    # paid/renewed tier is visible on the very next render (without waiting
    # for the natural 60s TTL).
    try:
        from fiesta.paywall.gate import invalidate_subscription_cache
        invalidate_subscription_cache(row.user_id)
    except Exception as exc:
        log.debug("invoice.paid: paywall cache invalidate skipped: %s", exc)

    # Tier D3 / C5 — close any open dunning rows for this invoice. The same
    # invoice can fail multiple times before succeeding; mark all of them as
    # recovered so should_show_banner flips off for the user.
    try:
        from dunning_sequence import mark_invoice_paid
        invoice_id = invoice.get("id") or ""
        if invoice_id:
            n = mark_invoice_paid(invoice_id)
            if n:
                log.info(
                    "invoice.paid: closed %s dunning row(s) for invoice=%s",
                    n, invoice_id[:32],
                )
    except Exception as exc:
        log.debug("invoice.paid: dunning recovery skipped: %s", exc)

    # Tier D4 / A5 — lifecycle drip enrollment (payment_thanks + deadlines).
    try:
        if row.user_id:
            from models import User
            from lifecycle_drip import enroll as _drip_enroll
            u = User.query.get(row.user_id)
            if u is not None:
                _drip_enroll(u, "payment_completed")
                _drip_enroll(u, "tax_year_cycle")
    except Exception as exc:
        log.debug("invoice.paid: lifecycle drip enroll skipped: %s", exc)

    # Tier D4 / A3 — referral credit (20% off referrer's next invoice).
    try:
        from referral_hook import apply_referral_credit_on_invoice_paid
        apply_referral_credit_on_invoice_paid(
            referee_user_id=row.user_id,
            stripe_subscription_id=stripe_subscription_id,
        )
    except Exception as exc:
        log.debug("invoice.paid: referral credit skipped: %s", exc)

    emit_analytics_event(
        "subscription_invoice_paid",
        user_id=row.user_id,
        payload={
            "subscription_id": row.id,
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_invoice_id": invoice.get("id"),
            "amount_paid": invoice.get("amount_paid"),
            "currency": invoice.get("currency"),
            "current_period_end": (
                row.current_period_end.isoformat() if row.current_period_end else None
            ),
        },
        source="webhook:stripe_subscription.invoice_paid",
    )


def _handle_payment_failed(stripe_event: dict) -> None:
    """``invoice.payment_failed`` — flip status='dunning'. Stub email; C5
    Wave 3 wires the actual customer message."""
    invoice = stripe_event.get("data", {}).get("object", {}) or {}
    stripe_subscription_id = invoice.get("subscription") or ""
    if not stripe_subscription_id:
        log.info("invoice.payment_failed: no subscription id — skipping")
        return

    from fiesta.paywall.models import Subscription
    row = (
        Subscription.query
        .filter_by(stripe_subscription_id=stripe_subscription_id)
        .first()
    )
    if row is None:
        log.info(
            "invoice.payment_failed: no Subscription row for sub=%s — skipping",
            stripe_subscription_id[:32],
        )
        return

    row.status = "dunning"

    from app import db
    db.session.commit()

    # Tier D6 / D8 — bust the per-user paywall tier cache (status moved to
    # dunning => downstream gating should re-evaluate; access stays valid until
    # expires_at so this is mostly a freshness signal).
    try:
        from fiesta.paywall.gate import invalidate_subscription_cache
        invalidate_subscription_cache(row.user_id)
    except Exception as exc:
        log.debug("invoice.payment_failed: paywall cache invalidate skipped: %s", exc)

    # Tier D3 / C5 — record the failure + alert CEO via Telegram. The
    # webhook's state-flip above stays the source of truth for Stripe
    # subscription status; the Dunning row is the per-invoice audit + banner
    # gate.
    attempt_count = invoice.get("attempt_count") or 1
    next_retry_unix = invoice.get("next_payment_attempt")
    next_retry_at = None
    if next_retry_unix:
        try:
            next_retry_at = datetime.utcfromtimestamp(next_retry_unix)
        except Exception:
            next_retry_at = None
    try:
        from dunning_sequence import record_failed_payment
        record_failed_payment(
            user_id=row.user_id,
            subscription_id=row.id,
            stripe_invoice_id=invoice.get("id") or "",
            attempt_count=attempt_count,
            next_retry_at=next_retry_at,
        )
    except Exception as exc:
        log.warning("invoice.payment_failed: dunning record failed: %s", exc)

    emit_analytics_event(
        "subscription_payment_failed",
        user_id=row.user_id,
        payload={
            "subscription_id": row.id,
            "stripe_subscription_id": stripe_subscription_id,
            "stripe_invoice_id": invoice.get("id"),
            "amount_due": invoice.get("amount_due"),
            "attempt_count": attempt_count,
            "next_payment_attempt": next_retry_unix,
            # C5 Wave 3 now records a Dunning row + fires Telegram via
            # dunning_sequence.record_failed_payment above. SES/Mailgun
            # customer-email delivery is the remaining follow-up (see
            # dunning_sequence.py TODO).
            "dunning_recorded": True,
        },
        source="webhook:stripe_subscription.payment_failed",
    )


def _handle_subscription_updated(stripe_event: dict) -> None:
    """``customer.subscription.updated`` — mirror cancel_at_period_end and
    current_period_end. Triggered when the customer toggles auto-renew via
    the billing portal."""
    sub = stripe_event.get("data", {}).get("object", {}) or {}
    stripe_subscription_id = sub.get("id") or ""
    if not stripe_subscription_id:
        return

    from fiesta.paywall.models import Subscription
    row = (
        Subscription.query
        .filter_by(stripe_subscription_id=stripe_subscription_id)
        .first()
    )
    if row is None:
        # First we've heard of this subscription — provision it.
        row = _get_or_create_subscription(
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=sub.get("customer") or None,
            user_id=_extract_user_id(sub),
        )
        if row is None:
            return

    cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    row.cancel_at_period_end = cancel_at_period_end
    # When the user toggles cancel via the portal, we leave auto_renew=True
    # until current_period_end actually arrives + Stripe sends the
    # subscription.deleted event. This matches Stripe's own model.

    period_end_ts = sub.get("current_period_end")
    if period_end_ts:
        new_end = datetime.utcfromtimestamp(period_end_ts)
        row.current_period_end = new_end
        row.expires_at = new_end

    status = sub.get("status") or ""
    if status == "past_due":
        row.status = "dunning"
    elif status == "active":
        row.status = "active"
    # "canceled" status arrives on subscription.deleted — handled there.

    from app import db
    db.session.commit()

    # Tier D6 / D8 — bust the per-user paywall tier cache.
    try:
        from fiesta.paywall.gate import invalidate_subscription_cache
        invalidate_subscription_cache(row.user_id)
    except Exception as exc:
        log.debug("subscription.updated: paywall cache invalidate skipped: %s", exc)

    emit_analytics_event(
        "subscription_updated",
        user_id=row.user_id,
        payload={
            "subscription_id": row.id,
            "stripe_subscription_id": stripe_subscription_id,
            "cancel_at_period_end": cancel_at_period_end,
            "stripe_status": status,
            "current_period_end": (
                row.current_period_end.isoformat() if row.current_period_end else None
            ),
        },
        source="webhook:stripe_subscription.updated",
    )


def _handle_subscription_deleted(stripe_event: dict) -> None:
    """``customer.subscription.deleted`` — subscription has ended. Access
    remains valid until current_period_end (Stripe behaviour); we just flip
    status='cancelled' + auto_renew=False so future renewals don't fire."""
    sub = stripe_event.get("data", {}).get("object", {}) or {}
    stripe_subscription_id = sub.get("id") or ""
    if not stripe_subscription_id:
        return

    from fiesta.paywall.models import Subscription
    row = (
        Subscription.query
        .filter_by(stripe_subscription_id=stripe_subscription_id)
        .first()
    )
    if row is None:
        log.info(
            "subscription.deleted: no Subscription for sub=%s",
            stripe_subscription_id[:32],
        )
        return

    row.status = "cancelled"
    row.auto_renew = False
    row.cancel_at_period_end = True

    from app import db
    db.session.commit()

    # Tier D6 / D8 — bust the per-user paywall tier cache. Access stays valid
    # until expires_at; clearing the cache makes future lookups re-check status.
    try:
        from fiesta.paywall.gate import invalidate_subscription_cache
        invalidate_subscription_cache(row.user_id)
    except Exception as exc:
        log.debug("subscription.deleted: paywall cache invalidate skipped: %s", exc)

    emit_analytics_event(
        "subscription_cancelled",
        user_id=row.user_id,
        payload={
            "subscription_id": row.id,
            "stripe_subscription_id": stripe_subscription_id,
            "current_period_end": (
                row.current_period_end.isoformat() if row.current_period_end else None
            ),
            "cancellation_reason": (
                sub.get("cancellation_details", {}).get("reason")
                if isinstance(sub.get("cancellation_details"), dict) else None
            ),
        },
        source="webhook:stripe_subscription.deleted",
    )


_EVENT_HANDLERS = {
    "invoice.paid": _handle_invoice_paid,
    "invoice.payment_failed": _handle_payment_failed,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
}


# --------------------------------------------------------------------------- #
# Webhook entrypoint.
# --------------------------------------------------------------------------- #

@subscription_bp.route("/webhooks/stripe/subscription", methods=["POST"])
def stripe_subscription_webhook():
    """Receive + verify + dispatch a Stripe subscription-mode webhook event.

    Response codes:
      * 200 -- event accepted (handled or knowingly ignored or duplicate).
      * 401 -- signature verification failed (Tier D1 spec requested 401,
               whereas the X1 paywall uses 400; matching the spec here).
      * 503 -- Stripe SDK / webhook secret missing in this environment.
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe  # type: ignore
    except ImportError:
        log.error("stripe SDK not installed; subscription webhook rejected")
        return jsonify({"error": "stripe SDK not installed"}), 503

    secret = _webhook_secret()
    if not secret:
        log.error(
            "STRIPE_SUBSCRIPTION_WEBHOOK_SECRET (and fallbacks) not set; "
            "subscription webhook rejected"
        )
        return jsonify({"error": "webhook secret not configured"}), 503

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as exc:
        log.warning("subscription webhook: invalid payload: %s", exc)
        return jsonify({"error": "invalid payload"}), 401
    except Exception as exc:
        # stripe.error.SignatureVerificationError + anything else => 401.
        log.warning("subscription webhook: signature verify failed: %s", exc)
        return jsonify({"error": "signature verification failed"}), 401

    event_id = stripe_event.get("id", "")
    event_type = stripe_event.get("type", "")

    if _stripe_event_already_handled(event_id):
        log.info("subscription webhook: duplicate event_id=%s ignored", event_id)
        return jsonify({"received": True, "duplicate": True}), 200

    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        log.info(
            "subscription webhook: ignored event_type=%s id=%s",
            event_type, event_id,
        )
        _mark_stripe_event(event_id, event_type, handled=False)
        return jsonify({"received": True, "handled": False}), 200

    try:
        handler(stripe_event)
    except Exception as exc:
        log.exception(
            "subscription webhook handler crashed for event_type=%s id=%s: %s",
            event_type, event_id, exc,
        )
        _mark_stripe_event(event_id, event_type, handled=False, error=str(exc))
        emit_analytics_event(
            "subscription_webhook_handler_error",
            user_id=None,
            payload={
                "stripe_event_id": event_id,
                "event_type": event_type,
                "error": str(exc)[:500],
            },
            source="webhook:stripe_subscription.error",
        )
        # 200: Stripe retries are expensive and would burn quota. The
        # error is surfaced via analytics + the tombstone row.
        return jsonify({
            "received": True, "handled": False, "error": "handler_failed",
        }), 200

    _mark_stripe_event(event_id, event_type, handled=True)
    return jsonify({"received": True, "handled": True}), 200


# --------------------------------------------------------------------------- #
# Customer billing portal redirect.
# --------------------------------------------------------------------------- #

@subscription_bp.route("/billing", methods=["GET"])
@login_required
def billing_portal():
    """Create a Stripe Customer Portal Session for the current user + redirect.

    Behaviour:
      * If the user has a Subscription row with stripe_customer_id, create a
        Billing Portal Session against that customer and 302 to its url.
      * If not, render templates/billing/no_subscription.html so the user
        can buy first.
      * Stripe SDK / secret missing -> flash + redirect to dashboard
        (consistent with the existing /pricing/x1/checkout failure mode).
    """
    from fiesta.paywall.models import Subscription
    if Subscription is None:
        log.error("/billing: Subscription model not registered")
        flash("Billing is temporarily unavailable.", "warning")
        return redirect(url_for("dashboard") if _has_endpoint("dashboard") else "/"), 503

    row = (
        Subscription.query
        .filter(Subscription.user_id == current_user.id)
        .filter(Subscription.stripe_customer_id.isnot(None))
        .order_by(Subscription.purchased_at.desc())
        .first()
    )
    if row is None or not row.stripe_customer_id:
        # User has no Stripe customer yet — render a helpful page that
        # points them at /pricing/x1.
        return render_template(
            "billing/no_subscription.html",
        )

    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("/billing: stripe SDK not installed")
        flash("Billing is temporarily unavailable.", "warning")
        return redirect("/"), 503

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        log.warning("/billing: STRIPE_SECRET_KEY not configured")
        flash("Billing is temporarily unavailable.", "warning")
        return redirect("/"), 503

    stripe.api_key = secret_key

    try:
        return_url = url_for(
            "billing_return", _external=True,
        ) if _has_endpoint("billing_return") else request.host_url
        session = stripe.billing_portal.Session.create(
            customer=row.stripe_customer_id,
            return_url=return_url,
        )
    except Exception as exc:
        log.exception("/billing: portal session create failed: %s", exc)
        flash("We couldn't open the billing portal. Please email support.", "danger")
        return redirect("/")

    emit_analytics_event(
        "billing_portal_opened",
        user_id=current_user.id,
        payload={
            "subscription_id": row.id,
            "stripe_customer_id": row.stripe_customer_id,
        },
        source="route:stripe_subscription.billing_portal",
    )

    return redirect(session.url, code=303)


@subscription_bp.route("/billing/return", methods=["GET"])
@login_required
def billing_return():
    """Post-portal landing page. Stripe redirects users here after they
    finish in the portal. Just acknowledges + sends them back to the app.
    """
    emit_analytics_event(
        "billing_portal_returned",
        user_id=current_user.id,
        payload={},
        source="route:stripe_subscription.billing_return",
    )
    return render_template("billing/return.html")


def _has_endpoint(endpoint: str) -> bool:
    try:
        return endpoint in (current_app.view_functions or {})
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Registration helper.
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register the subscription blueprint + CSRF-exempt the webhook.

    Idempotent. Safe to call from main.py at orchestrator wire-time.
    """
    if "stripe_subscription" in app.blueprints:
        return
    app.register_blueprint(subscription_bp)

    # Exempt the webhook route from CSRF — Stripe signs the body, not a token.
    try:
        from app import csrf
        try:
            csrf.exempt(subscription_bp)
        except Exception:
            csrf.exempt(stripe_subscription_webhook)
        log.info("stripe_subscription webhook CSRF-exempted")
    except Exception as exc:
        log.warning(
            "Could not CSRF-exempt stripe_subscription_bp (%s); webhook may 400",
            exc,
        )

    log.info(
        "Stripe subscription routes registered "
        "(/webhooks/stripe/subscription, /billing, /billing/return)"
    )


__all__ = [
    "subscription_bp",
    "register_routes",
    "stripe_subscription_webhook",
    "billing_portal",
]
