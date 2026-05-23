"""
fiesta.paywall.pricing_screen — X1 paywall pricing surface + Stripe webhook.

Routes:

  GET  /pricing/x1                           -> render the X1 pricing screen.
                                                ?return_to=...&screen_id=...
                                                are echoed into the template
                                                so the post-purchase return
                                                preserves intent.
  POST /pricing/x1/checkout                  -> create Stripe Checkout Session
                                                in payment mode (one-time),
                                                redirect to Stripe.
  GET  /pricing/x1/success                   -> post-checkout courtesy page.
                                                The Subscription row is
                                                authoritatively created by the
                                                webhook, not this view.
  POST /webhooks/stripe/paywall              -> Stripe webhook for X1 events.
                                                Idempotent via paywall_stripe_event.

Distinct from the legacy ``/pricing`` route (pricing_engine.py): that's the
3-tier subscription product. ``/pricing/x1`` is the unified Self-File one-time
purchase.

The blueprint registers under url_prefix='' so the pretty URL is /pricing/x1.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from flask import (
    Blueprint, request, redirect, url_for, render_template, jsonify, flash,
    abort, current_app,
)
from flask_login import login_required, current_user

from events import emit as emit_analytics_event
from .models import (
    SELF_FILE_PRICE_LKR, SELF_FILE_PRICE_CENTS,
    TIER_SELF_FILE, current_sl_tax_year, expires_at_for_tax_year,
)
from .stripe_config import (
    log_startup_stripe_status,
    validate_stripe_config,
)

log = logging.getLogger(__name__)


paywall_bp = Blueprint("paywall", __name__, url_prefix="")


# --------------------------------------------------------------------------- #
# Public copy (X1 unified product). One source of truth — the JSON endpoint
# below mirrors this dict for landing pages / docs.
# --------------------------------------------------------------------------- #
SELF_FILE_PRODUCT = {
    "key": TIER_SELF_FILE,
    "name": "Self-File Tax Return",
    "tagline": "Audit-defensible documents. §195 compliance. One-time payment.",
    "price_lkr": SELF_FILE_PRICE_LKR,
    "price_display": f"Rs {SELF_FILE_PRICE_LKR:,}",
    "billing_model": "one_time",
    "refund_window_days": 14,
    "scope": "Tax-year-bounded (expires 31 Mar of the following tax year)",
    "features": [
        "Service Agreement generation for every deduction",
        "Automatic §195 compliance checks",
        "Audit-defensible documentation bundle",
        "Tax bill computation + IRD-ready submission pack",
        "Refundable within 14 days of purchase",
    ],
    "cta": "Unlock Self-File - Rs 2,500",
}


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #

def _safe_return_to() -> Optional[str]:
    """Read ?return_to from the request; reject open-redirects to other hosts."""
    rt = request.args.get("return_to") or request.form.get("return_to")
    if not rt:
        return None
    # Strict allow-list: must be a same-origin relative path.
    if not rt.startswith("/"):
        return None
    if rt.startswith("//"):
        return None  # protocol-relative URL -> open redirect risk
    return rt


def _safe_screen_id() -> Optional[str]:
    sid = request.args.get("screen_id") or request.form.get("screen_id")
    if not sid:
        return None
    # Whitelist S\d+ shapes only; reject everything else.
    if len(sid) > 8:
        return None
    if not (sid.startswith("S") and sid[1:].isdigit()):
        return None
    return sid


# --------------------------------------------------------------------------- #
# Routes — pricing screen.
# --------------------------------------------------------------------------- #

@paywall_bp.route("/pricing/x1", methods=["GET"])
def pricing_screen():
    """Render the X1 paywall pricing screen.

    Public route — anonymous users can browse pricing. Authenticated users see
    a personalised CTA hint if they're on a paywall-redirect flow.

    X9 F7.1 — the paywall now reads "save Rs X for Rs 2,500" instead of just
    listing Rs 2,500. Authenticated users get their actual projected saving
    computed from their RemittanceEntry rows; anonymous users get the
    Rs 540K median from worked-examples (per the worked_examples.json
    Lanka.tax sample, n=78 foreign-income filers).
    """
    return_to = _safe_return_to()
    screen_id = _safe_screen_id()

    is_auth = bool(getattr(current_user, "is_authenticated", False))

    # X9 F7.1 — projected_savings_lkr feeds the "save Rs X" reframe on the
    # template. Median anon saving Rs 540K; authed users get their own
    # computed projection from the hub context where available.
    projected_savings_lkr = 540_000
    if is_auth:
        try:
            from flask import g
            personal = int(getattr(g, "hub_projected_savings_lkr", 0) or 0)
            if personal > 0:
                projected_savings_lkr = personal
        except Exception:
            pass
        # Fall back to the same compute path used by inject_fiesta_hub_context
        # if g.hub_* wasn't populated (e.g. non-FIESTA persona hitting the paywall).
        if projected_savings_lkr == 540_000:
            try:
                from fiesta.earnings.to_tax import income_summary_for_tax_year
                from fiesta.paywall.models import current_sl_tax_year
                ty = current_sl_tax_year()
                ty_s4 = ty.replace("/", "-") if "/" in ty else ty
                summary = income_summary_for_tax_year(current_user.id, ty_s4)
                total_lkr = float(summary.get("total_lkr") or 0)
                if total_lkr > 0:
                    projected_savings_lkr = max(int(total_lkr * 0.033), 540_000)
            except Exception:
                pass

    emit_analytics_event(
        "pricing_x1_page_viewed",
        user_id=current_user.id if is_auth else None,
        payload={
            "return_to": return_to,
            "screen_id": screen_id,
            "authenticated": is_auth,
            "projected_savings_lkr": projected_savings_lkr,
        },
        source="route:paywall.pricing_screen",
    )

    # A10 F7.2 — context-aware banner copy per screen_id.
    _SCREEN_ID_COPY = {
        "S6":  "generate Service Agreements",
        "S7":  "generate Rental Agreements + home-office rent calc",
        "S12": "see your bracket-by-bracket tax bill",
        "S14": "submit the IRD-ready filing pack",
    }
    screen_id_copy = _SCREEN_ID_COPY.get(screen_id) if screen_id else None

    return render_template(
        "paywall/pricing_x1.html",
        product=SELF_FILE_PRODUCT,
        return_to=return_to,
        screen_id=screen_id,
        screen_id_copy=screen_id_copy,
        authenticated=is_auth,
        projected_savings_lkr=projected_savings_lkr,
    )


@paywall_bp.route("/pricing/x1/checkout", methods=["POST"])
@login_required
def checkout():
    """Create a Stripe Checkout Session (mode=payment, one-time) and redirect."""
    return_to = _safe_return_to() or "/dashboard"
    screen_id = _safe_screen_id()
    tax_year = current_sl_tax_year()

    emit_analytics_event(
        "paywall_checkout_started",
        user_id=current_user.id,
        payload={
            "tier": TIER_SELF_FILE,
            "return_to": return_to,
            "screen_id": screen_id,
            "tax_year": tax_year,
            "price_lkr": SELF_FILE_PRICE_LKR,
        },
        source="route:paywall.checkout",
    )

    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("stripe SDK not installed; cannot create paywall checkout")
        flash(
            "Checkout is temporarily unavailable. Please try again later.",
            "warning",
        )
        return redirect(url_for("paywall.pricing_screen")), 503

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        log.warning("STRIPE_SECRET_KEY not configured for paywall checkout")
        flash(
            "Checkout is temporarily unavailable. Please try again later.",
            "warning",
        )
        return redirect(url_for("paywall.pricing_screen")), 503

    stripe.api_key = secret_key

    try:
        # Build absolute success/cancel URLs. Stripe requires absolute.
        success_url = url_for(
            "paywall.checkout_success",
            session_id="{CHECKOUT_SESSION_ID}",
            return_to=return_to,
            _external=True,
        )
        cancel_url = url_for(
            "paywall.pricing_screen",
            return_to=return_to,
            screen_id=screen_id or "",
            _external=True,
        )

        session = stripe.checkout.Session.create(
            mode="payment",  # one-time, NOT subscription
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "lkr",
                    "product_data": {
                        "name": SELF_FILE_PRODUCT["name"],
                        "description": SELF_FILE_PRODUCT["tagline"],
                    },
                    "unit_amount": SELF_FILE_PRICE_CENTS,
                },
                "quantity": 1,
            }],
            customer_email=getattr(current_user, "email", None),
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(current_user.id),
                "tier": TIER_SELF_FILE,
                "tax_year": tax_year,
                "return_to": return_to,
                "screen_id": screen_id or "",
                "paywall_event_id": str(
                    request.form.get("paywall_event_id", "")
                ),
            },
        )
    except Exception as exc:
        log.exception("paywall stripe checkout failed: %s", exc)
        flash(
            "Checkout couldn't start. Please try again, or email support.",
            "danger",
        )
        return redirect(url_for("paywall.pricing_screen", return_to=return_to))

    return redirect(session.url, code=303)


@paywall_bp.route("/pricing/x1/success", methods=["GET"])
@login_required
def checkout_success():
    """Courtesy landing page after Stripe returns the user.

    Authority for "is this user paid?" sits in the webhook — this view never
    creates a Subscription row. It just shows a "thanks, we're processing"
    message and (if the Subscription row already exists from the webhook,
    which usually arrives milliseconds before the user returns) routes them
    back to the original screen.
    """
    return_to = _safe_return_to() or "/dashboard"
    session_id = request.args.get("session_id", "")

    # Best-effort: if the subscription is already active, route straight to
    # return_to. Otherwise show the "processing" page (will refresh in 3s).
    from .gate import is_tier_active
    if is_tier_active(current_user, TIER_SELF_FILE):
        emit_analytics_event(
            "paywall_checkout_returned_active",
            user_id=current_user.id,
            payload={"session_id": session_id[:64], "return_to": return_to},
            source="route:paywall.checkout_success",
        )
        return redirect(return_to)

    emit_analytics_event(
        "paywall_checkout_returned_pending",
        user_id=current_user.id,
        payload={"session_id": session_id[:64], "return_to": return_to},
        source="route:paywall.checkout_success",
    )
    # Sprint 4 Tier A: pass subscription_active + tax_year_display to the
    # rewritten welcome template so we can show the right badge state and
    # the right tax year copy. The template polls /pricing/x1.json every 2s
    # to flip from "Confirming" -> "Active" if the webhook lands late.
    return render_template(
        "paywall/checkout_success.html",
        return_to=return_to,
        session_id=session_id,
        subscription_active=False,
        tax_year_display=current_sl_tax_year(),
    )


@paywall_bp.route("/pricing/x1.json", methods=["GET"])
def product_json():
    """Machine-readable product spec — for landing pages, the AI orchestrator,
    and the funnel admin views.

    Sprint 4 Tier A: also include subscription_active for the
    checkout-success page's polling JS to flip 'Confirming' -> 'Active'
    once the Stripe webhook lands.
    """
    from .gate import is_tier_active
    subscription_active = False
    try:
        if current_user.is_authenticated:
            subscription_active = bool(is_tier_active(current_user, TIER_SELF_FILE))
    except Exception:
        pass
    return jsonify({
        "product": SELF_FILE_PRODUCT,
        "tax_year": current_sl_tax_year(),
        "expires_at_iso": expires_at_for_tax_year().isoformat(),
        "subscription_active": subscription_active,
    })


# --------------------------------------------------------------------------- #
# Stripe webhook — idempotent.
# --------------------------------------------------------------------------- #

@paywall_bp.route("/webhooks/stripe/paywall", methods=["POST"])
def stripe_webhook():
    """X1 Stripe webhook. Handles ``checkout.session.completed`` and
    ``charge.refunded`` events. Idempotent via the ``paywall_stripe_event``
    tombstone table.

    Returns 200 for accepted/duplicate events, 400 for signature failures,
    503 if Stripe SDK / secret is missing.
    """
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe  # type: ignore
    except ImportError:
        log.error("stripe SDK not installed; paywall webhook rejected")
        return jsonify({"error": "stripe SDK not installed"}), 503

    secret = (
        os.environ.get("STRIPE_PAYWALL_WEBHOOK_SECRET")
        or os.environ.get("STRIPE_WEBHOOK_SECRET")
    )
    if not secret:
        log.error("STRIPE_PAYWALL_WEBHOOK_SECRET not set; webhook rejected")
        return jsonify({"error": "webhook secret not configured"}), 503

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError as exc:
        log.warning("paywall webhook invalid payload: %s", exc)
        return jsonify({"error": "invalid payload"}), 400
    except Exception as exc:
        log.warning("paywall webhook signature verify failed: %s", exc)
        return jsonify({"error": "signature verification failed"}), 400

    event_id = stripe_event.get("id", "")
    event_type = stripe_event.get("type", "")

    # Idempotency check — short-circuit if we've already handled this id.
    if _stripe_event_already_handled(event_id):
        log.info("paywall webhook: duplicate event_id=%s ignored", event_id)
        return jsonify({"received": True, "duplicate": True}), 200

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(stripe_event)
        elif event_type == "charge.refunded":
            _handle_charge_refunded(stripe_event)
        else:
            log.info("paywall webhook: ignored event_type=%s id=%s",
                     event_type, event_id)
            _mark_stripe_event(event_id, event_type, handled=False)
            return jsonify({"received": True, "handled": False}), 200

        _mark_stripe_event(event_id, event_type, handled=True)
        return jsonify({"received": True, "handled": True}), 200
    except Exception as exc:
        log.exception("paywall webhook handler crashed: %s", exc)
        _mark_stripe_event(event_id, event_type, handled=False, error=str(exc))
        # Return 200 anyway — Stripe retries would burn quota; we'd rather
        # surface the failure in the dashboard.
        return jsonify({"received": True, "handled": False, "error": "handler_failed"}), 200


# --------------------------------------------------------------------------- #
# Webhook handlers + idempotency helpers.
# --------------------------------------------------------------------------- #

def _stripe_event_already_handled(event_id: str) -> bool:
    if not event_id:
        return False
    try:
        from .models import StripeEvent
        if StripeEvent is None:
            return False
        return StripeEvent.query.filter_by(stripe_event_id=event_id).first() is not None
    except Exception as exc:
        log.warning("stripe-event dedup check failed: %s", exc)
        return False


def _mark_stripe_event(event_id: str, event_type: str,
                      handled: bool, error: Optional[str] = None) -> None:
    if not event_id:
        return
    try:
        from app import db
        from .models import StripeEvent
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
        log.warning("stripe_event mark failed: %s", exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass


def _handle_checkout_completed(stripe_event: dict) -> None:
    """Create the Subscription row + mark any preceding PaywallEvent as converted."""
    session = stripe_event.get("data", {}).get("object", {}) or {}
    metadata = session.get("metadata") or {}

    user_id_str = metadata.get("user_id")
    tier = metadata.get("tier") or TIER_SELF_FILE
    tax_year = metadata.get("tax_year") or current_sl_tax_year()
    paywall_event_id_str = metadata.get("paywall_event_id") or ""
    amount_total_cents = session.get("amount_total") or 0
    payment_intent_id = session.get("payment_intent") or ""
    session_id = session.get("id") or ""

    try:
        user_id = int(user_id_str) if user_id_str else None
    except (TypeError, ValueError):
        user_id = None
    try:
        paywall_event_id = int(paywall_event_id_str) if paywall_event_id_str else None
    except (TypeError, ValueError):
        paywall_event_id = None

    if user_id is None:
        log.warning("paywall webhook: checkout.completed with no user_id "
                    "(session=%s)", session_id[:32])
        return

    from app import db
    from .models import Subscription, PaywallEvent

    # Idempotency at the Subscription level — if we already minted a row
    # for this payment_intent, do nothing.
    if payment_intent_id:
        existing = Subscription.query.filter_by(
            stripe_payment_intent_id=payment_intent_id
        ).first()
        if existing is not None:
            log.info("paywall webhook: subscription already exists for "
                     "payment_intent=%s — no-op", payment_intent_id[:32])
            return

    sub = Subscription(
        user_id=user_id,
        tier=tier,
        tax_year=tax_year,
        purchased_at=datetime.utcnow(),
        expires_at=expires_at_for_tax_year(tax_year),
        stripe_payment_intent_id=payment_intent_id or None,
        stripe_session_id=session_id or None,
        amount_paid_lkr=int(amount_total_cents / 100) if amount_total_cents else None,
        status="active",
        triggering_paywall_event_id=paywall_event_id,
    )
    db.session.add(sub)
    db.session.flush()

    # Mark the originating PaywallEvent (or the most recent fire for this
    # user, as a fallback) as converted.
    target_event = None
    if paywall_event_id:
        target_event = PaywallEvent.query.get(paywall_event_id)
    if target_event is None:
        target_event = (
            PaywallEvent.query
            .filter(PaywallEvent.user_id == user_id)
            .filter(PaywallEvent.converted_at.is_(None))
            .order_by(PaywallEvent.fired_at.desc())
            .first()
        )
    if target_event is not None:
        target_event.converted_at = datetime.utcnow()
        target_event.conversion_revenue_lkr = sub.amount_paid_lkr

    db.session.commit()

    emit_analytics_event(
        "paywall_checkout_completed",
        user_id=user_id,
        payload={
            "tier": tier,
            "tax_year": tax_year,
            "subscription_id": sub.id,
            "amount_paid_lkr": sub.amount_paid_lkr,
            "stripe_payment_intent_id": payment_intent_id,
            "stripe_session_id": session_id,
            "paywall_event_id": target_event.id if target_event else None,
        },
        source="webhook:paywall.checkout_completed",
    )


def _handle_charge_refunded(stripe_event: dict) -> None:
    """Mark Subscription refunded + status='refunded' on charge.refunded events.

    The refund window is policy-enforced (14 days) — Stripe doesn't enforce
    it for us. We honour any refund that comes in; the support team is the
    gate for "is this within the window?".
    """
    charge = stripe_event.get("data", {}).get("object", {}) or {}
    payment_intent_id = charge.get("payment_intent") or ""
    if not payment_intent_id:
        log.info("paywall webhook: charge.refunded with no payment_intent — skipping")
        return

    from app import db
    from .models import Subscription
    sub = Subscription.query.filter_by(
        stripe_payment_intent_id=payment_intent_id
    ).first()
    if sub is None:
        log.info("paywall webhook: refund for unknown payment_intent=%s",
                 payment_intent_id[:32])
        return

    sub.status = "refunded"
    sub.refunded_at = datetime.utcnow()
    db.session.commit()

    emit_analytics_event(
        "paywall_subscription_refunded",
        user_id=sub.user_id,
        payload={
            "subscription_id": sub.id,
            "stripe_payment_intent_id": payment_intent_id,
        },
        source="webhook:paywall.charge_refunded",
    )


# --------------------------------------------------------------------------- #
# Health endpoint — Stripe key-mode indicator (added 2026-05-20 for v1 deploy).
# --------------------------------------------------------------------------- #

@paywall_bp.route("/healthz/stripe", methods=["GET"])
def stripe_healthz():
    """Report Stripe-key-mode (test vs live) + readiness.

    JSON shape (see fiesta.paywall.stripe_config.validate_stripe_config):

        {
          "mode":               "live" | "test" | "missing" | "unknown",
          "webhook":            "configured" | "missing",
          "ready":              bool,
          "live_required":      bool,
          "issues":             [str, ...],
          "warnings":           [str, ...],
          "live_webhook_match": bool | null
        }

    HTTP status:
        200  -- ready=True (paywall is operational)
        503  -- ready=False (issues present; webhook/checkout will 503)

    Designed to be polled by external monitoring AND eyeballed by the
    operator immediately before/after a live-keys swap.
    """
    snapshot = validate_stripe_config()
    status_code = 200 if snapshot["ready"] else 503
    return jsonify(snapshot), status_code


# --------------------------------------------------------------------------- #
# Registration helper — orchestrator calls this from main.py.
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register the paywall blueprint + CSRF-exempt the webhook."""
    from .models import register_models
    register_models()

    app.register_blueprint(paywall_bp)

    # Exempt the webhook from CSRF — Stripe signs the body, not a token.
    try:
        from app import csrf
        # CSRFProtect.exempt accepts a view function, not a blueprint, in some
        # versions — try blueprint first, fall back to per-view exemption.
        try:
            csrf.exempt(paywall_bp)
        except Exception:
            csrf.exempt(stripe_webhook)
        log.info("paywall.stripe_webhook CSRF-exempted")
    except Exception as exc:
        log.warning("Could not CSRF-exempt paywall webhook (%s); POSTs may 400",
                    exc)

    # Stripe key-mode startup validation (non-fatal in dev, surfaces issues
    # in the app log so the operator sees them at boot before any traffic).
    try:
        log_startup_stripe_status(app)
    except Exception as exc:  # pragma: no cover -- defensive only
        log.warning("Stripe startup validation hook crashed: %s", exc)

    log.info("Paywall X1 routes registered "
             "(/pricing/x1, /pricing/x1/checkout, /webhooks/stripe/paywall, "
             "/healthz/stripe)")


__all__ = [
    "paywall_bp",
    "register_routes",
    "SELF_FILE_PRODUCT",
]
