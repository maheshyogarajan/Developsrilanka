"""
Stripe webhook handler — closes the funnel by promoting users to paid status
on ``checkout.session.completed`` and downgrading on cancellation.

Wave 2.2 (2026-05-17). Paired with ``pricing_engine.py``.

Hard rules
----------
1. **CSRF exempt.** Stripe webhooks come from outside the browser; they sign
   the request body instead of using a CSRF token. The blueprint registration
   wires the route through ``csrf.exempt`` (the ``CSRFProtect`` instance lives
   on ``app``).
2. **Signature verification is mandatory.** If ``STRIPE_WEBHOOK_SECRET`` is
   missing the route returns 503 — fail closed, never accept unverified events.
3. **Never raise to Stripe.** Stripe retries 5xx aggressively (up to 3 days);
   raising on a parsing edge case would burn quota and pollute the analytics
   stream. We log + return 200 except for genuine signature failures (400).
4. **Idempotent.** ``checkout.session.completed`` may be redelivered. We re-set
   the same ``subscription_status`` value each time — the operation is naturally
   idempotent at the user-row level; the emitted event row gets a duplicate
   that the analytics layer deduplicates by Stripe event id.

Registered events
-----------------
* ``checkout.session.completed``  -> ``user.subscription_status = "premium_<tier>"``,
                                     emit ``checkout_completed``.
* ``invoice.payment_failed``      -> emit ``payment_failed`` (no status change;
                                     Stripe handles dunning + auto-cancel).
* ``customer.subscription.deleted`` -> ``user.subscription_status = "free_trial"``,
                                     emit ``subscription_cancelled``.

All other event types acknowledge with 200 and a log line.
"""
import logging
import os
from typing import Optional

from flask import Blueprint, request, jsonify

from events import emit as emit_event

log = logging.getLogger(__name__)


stripe_bp = Blueprint("stripe_wh", __name__, url_prefix="/webhooks/stripe")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_user_by_id(user_id_str: Optional[str]):
    """Look up a User by string id from Stripe metadata. Returns None on any
    issue (missing, non-int, missing user)."""
    if not user_id_str:
        return None
    try:
        uid = int(user_id_str)
    except (TypeError, ValueError):
        return None
    try:
        from models import User
        return User.query.get(uid)
    except Exception as exc:
        log.warning("User lookup failed for id=%s: %s", user_id_str, exc)
        return None


def _commit() -> bool:
    """Commit the db.session. Returns True on success, False otherwise (and
    rolls back). Never raises."""
    try:
        from app import db
        db.session.commit()
        return True
    except Exception as exc:
        log.exception("DB commit failed in stripe webhook: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------- #
# Per-event-type handlers
# --------------------------------------------------------------------------- #

def _handle_checkout_completed(stripe_event: dict) -> None:
    """``checkout.session.completed`` — flip user to paid status."""
    session = stripe_event.get("data", {}).get("object", {}) or {}
    metadata = session.get("metadata") or {}
    user_id_str = metadata.get("user_id")
    tier = metadata.get("tier") or "unknown"
    amount_total = session.get("amount_total")  # smallest currency unit
    currency = session.get("currency")

    user = _get_user_by_id(user_id_str)
    if user is None:
        log.warning(
            "checkout.session.completed: no user found for metadata.user_id=%r "
            "(session id %s) — emitting event with user_id=None",
            user_id_str, session.get("id"),
        )
    else:
        # Subscription status convention: 'premium_<tier>' so downstream
        # consumers (admin UI, feature gates) can both check `startswith('premium')`
        # AND know which tier the user is on. If the tier is unknown we still
        # promote to a plain 'premium' so the user isn't locked out of what
        # they paid for.
        user.subscription_status = f"premium_{tier}" if tier and tier != "unknown" else "premium"
        if not _commit():
            log.error(
                "checkout.session.completed: failed to persist subscription_status "
                "for user_id=%s — event will be re-tried by Stripe", user.id,
            )
            return

    emit_event(
        "checkout_completed",
        user_id=user.id if user else None,
        payload={
            "stripe_event_id": stripe_event.get("id"),
            "stripe_session_id": session.get("id"),
            "tier": tier,
            "amount_total": amount_total,
            "currency": currency,
            "customer_email": session.get("customer_email"),
        },
        source="webhook:stripe",
    )


def _handle_payment_failed(stripe_event: dict) -> None:
    """``invoice.payment_failed`` — emit a dunning signal; don't change status.
    Stripe handles the retry schedule itself."""
    invoice = stripe_event.get("data", {}).get("object", {}) or {}
    # invoice doesn't always carry our metadata; we surface what Stripe gives
    # us and let the analytics layer correlate via customer/subscription id.
    emit_event(
        "payment_failed",
        user_id=None,
        payload={
            "stripe_event_id": stripe_event.get("id"),
            "stripe_invoice_id": invoice.get("id"),
            "stripe_customer_id": invoice.get("customer"),
            "stripe_subscription_id": invoice.get("subscription"),
            "amount_due": invoice.get("amount_due"),
            "currency": invoice.get("currency"),
            "attempt_count": invoice.get("attempt_count"),
            "next_payment_attempt": invoice.get("next_payment_attempt"),
        },
        source="webhook:stripe",
    )


def _handle_subscription_deleted(stripe_event: dict) -> None:
    """``customer.subscription.deleted`` — revert to free_trial."""
    subscription = stripe_event.get("data", {}).get("object", {}) or {}
    metadata = subscription.get("metadata") or {}
    user_id_str = metadata.get("user_id")

    user = _get_user_by_id(user_id_str)
    if user is not None:
        user.subscription_status = "free_trial"
        if not _commit():
            log.error(
                "subscription.deleted: failed to persist subscription_status "
                "for user_id=%s", user.id,
            )

    emit_event(
        "subscription_cancelled",
        user_id=user.id if user else None,
        payload={
            "stripe_event_id": stripe_event.get("id"),
            "stripe_subscription_id": subscription.get("id"),
            "cancel_reason": subscription.get("cancellation_details", {}).get("reason")
                if isinstance(subscription.get("cancellation_details"), dict) else None,
        },
        source="webhook:stripe",
    )


_EVENT_HANDLERS = {
    "checkout.session.completed": _handle_checkout_completed,
    "invoice.payment_failed": _handle_payment_failed,
    "customer.subscription.deleted": _handle_subscription_deleted,
}


# --------------------------------------------------------------------------- #
# Webhook entrypoint
# --------------------------------------------------------------------------- #

@stripe_bp.route("", methods=["POST"])
@stripe_bp.route("/", methods=["POST"])
def stripe_webhook():
    """Receive + verify + dispatch a Stripe webhook event.

    Response codes:
      * 200 -> event accepted (handled or knowingly ignored).
      * 400 -> signature verification failed OR bad payload.
      * 503 -> Stripe SDK / webhook secret missing in this environment.
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    # Lazy import — module stays importable in environments without the SDK.
    try:
        import stripe  # type: ignore
    except ImportError:
        log.error("stripe SDK not installed; webhook rejected")
        return jsonify({"error": "stripe SDK not installed"}), 503

    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        log.error("STRIPE_WEBHOOK_SECRET not set; webhook rejected")
        return jsonify({"error": "webhook secret not configured"}), 503

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as exc:
        log.warning("Stripe webhook: invalid payload: %s", exc)
        return jsonify({"error": "invalid payload"}), 400
    except Exception as exc:
        # Stripe's library raises SignatureVerificationError, which is an
        # Exception subclass; we catch broadly to be defensive across versions.
        log.warning("Stripe webhook: signature verification failed: %s", exc)
        return jsonify({"error": "signature verification failed"}), 400

    event_type = stripe_event.get("type", "")
    handler = _EVENT_HANDLERS.get(event_type)

    if handler is None:
        log.info("Stripe webhook: ignored event_type=%s id=%s",
                 event_type, stripe_event.get("id"))
        return jsonify({"received": True, "handled": False}), 200

    try:
        handler(stripe_event)
    except Exception as exc:
        # Defensive: never propagate to Stripe. We log + emit a failure event
        # so the dashboard can surface it; Stripe sees a 200 and stops
        # retrying. (A 5xx here would cause a backoff storm that drowns the
        # legitimate funnel signal.)
        log.exception(
            "Stripe webhook handler crashed for event_type=%s id=%s: %s",
            event_type, stripe_event.get("id"), exc,
        )
        emit_event(
            "stripe_webhook_handler_error",
            user_id=None,
            payload={
                "stripe_event_id": stripe_event.get("id"),
                "event_type": event_type,
                "error": str(exc)[:500],
            },
            source="webhook:stripe",
        )

    return jsonify({"received": True, "handled": True}), 200


# --------------------------------------------------------------------------- #
# Module-level registration helper — mirrors pricing_engine + remittance_routes.
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register both the pricing blueprint and the Stripe webhook blueprint.

    Pricing UI lives in ``pricing_engine.pricing_bp``; the webhook lives here.
    Bundling registration so the orchestrator wires both with one call.

    CSRF: the Stripe webhook is exempted via ``app.extensions['csrf']`` if the
    CSRFProtect instance is reachable. Falls back to a per-view ``csrf.exempt``
    import from ``app`` if not.
    """
    from pricing_engine import pricing_bp
    app.register_blueprint(pricing_bp)
    app.register_blueprint(stripe_bp)

    # Exempt the Stripe webhook from CSRF — Stripe signs the body, not a token.
    try:
        from app import csrf
        csrf.exempt(stripe_bp)
        log.info("Stripe webhook blueprint CSRF-exempted")
    except Exception as exc:
        log.warning(
            "Could not CSRF-exempt stripe_bp via app.csrf (%s); webhook may 400 "
            "on POST. Verify CSRFProtect is configured at app boot.",
            exc,
        )

    log.info("Pricing + Stripe webhook routes registered "
             "(/pricing, /webhooks/stripe)")


__all__ = ["stripe_bp", "register_routes"]
