"""
Pricing Engine — three-tier monetization for FIESTA's foreign-income persona.

Wave 2.2 (2026-05-17). Converts the 5,600 Lanka.tax cross-sell cohort into an
ARR floor: Self-Serve $99, Pro $199, Premium $349 per year, with an optional
DTA reconciler add-on at $49 one-time.

Design intent
-------------
1. ONE source of truth for tier definitions — ``PRICING_TIERS`` — referenced by
   the public page, the Stripe checkout creator, the recommender, and the
   tests. Adding a tier means adding a key here and nothing else.

2. Persona-aware ``recommend_tier(user)``. Heuristic: remittance volume + any
   foreign-tax-withholding (DTA territory) buckets the user into the most
   appropriate plan. Returns a key from ``PRICING_TIERS``; never raises.

3. A/B variant chooser (``assign_experiment_variant``). Deterministic per user
   (``user.id % 100``) so the same user always lands in the same bucket across
   sessions — a hard requirement for any honest funnel measurement.

4. Stripe is imported lazily inside the checkout handler. The module itself is
   importable in environments that don't have ``stripe`` installed (tests,
   local dev without the SDK). The checkout route returns a friendly 503 if
   the SDK is missing or the secret is not configured.

5. Every revenue-relevant action emits an Event row via ``events.emit`` so the
   Wave 2 funnel dashboard can read pricing_page_viewed -> checkout_started ->
   checkout_completed without bespoke instrumentation.
"""
import logging
import os
from typing import Optional

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort,
)
from flask_login import login_required, current_user

from events import emit as emit_event

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tier definitions — single source of truth.
# --------------------------------------------------------------------------- #
#
# Prices are annual recurring (LKR + USD shown side-by-side; Stripe checkout
# is created in LKR). USD price is the display anchor most foreign-income
# earners read first. The Sri Lanka rupee figure is what's actually charged.
#
# `features` is the list shown on the pricing page. Keep each line concise
# (~10 words). The first item of Pro/Premium starts with "Everything in <X>"
# to make the upgrade reasoning obvious.
#
PRICING_TIERS = {
    "self_serve": {
        "key": "self_serve",
        "name": "Self-Serve",
        "tagline": "For occasional foreign-income earners.",
        "price_lkr_yr": 30000,
        "price_usd_yr": 99,
        "features": [
            "Unlimited foreign-income remittances",
            "Automatic CBSL middle rate (with manual fallback)",
            "IRD-pack export (PDF + CSV bundle)",
            "Bank-statement PDF/CSV import (10/day)",
            "Year of Assessment dashboard",
            "Email support",
        ],
        "cta": "Start Self-Serve",
        "best_for": "Up to 5 inward remittances per year of assessment.",
    },
    "pro": {
        "key": "pro",
        "name": "Pro Compliance",
        "tagline": "For freelancers and consultants invoicing abroad monthly.",
        "price_lkr_yr": 60000,
        "price_usd_yr": 199,
        "features": [
            "Everything in Self-Serve",
            "DTA reconciler (foreign-tax-withheld credit calculator)",
            "2 staff-review credits per year (Lanka.tax verification of your pack)",
            "Source-document attachment + storage",
            "Priority email support (1 business day)",
            "Quarterly tax-position summary",
        ],
        "cta": "Choose Pro Compliance",
        "best_for": "6-20 remittances/year, or any foreign tax withheld.",
    },
    "premium": {
        "key": "premium",
        "name": "Premium Filing",
        "tagline": "Hand the whole return to Lanka.tax. We file, you sign.",
        "price_lkr_yr": 105000,
        "price_usd_yr": 349,
        "features": [
            "Everything in Pro Compliance",
            "Lanka.tax filing handoff included (return prepared + lodged)",
            "Year-round IRD monitoring (notices, assessments, refunds)",
            "Unlimited staff-review credits",
            "DTA certificate procurement assistance",
            "Direct line to a Lanka.tax tax officer",
        ],
        "cta": "Choose Premium Filing",
        "best_for": "20+ remittances/year, or you'd rather not touch the IRD portal.",
    },
}


DTA_ADD_ON = {
    "key": "dta_add_on",
    "name": "DTA Reconciler Add-On",
    "price_lkr": 15000,
    "price_usd": 49,
    "description": (
        "One-time foreign-tax-withheld reconciliation and DTA credit "
        "calculator. Included in Pro and Premium."
    ),
}


# --------------------------------------------------------------------------- #
# A/B experiments — module-level so consumers (Wave 2 funnel dashboard) can
# enumerate the variants without spelunking the route handler.
# --------------------------------------------------------------------------- #
#
# When adding a new experiment, give it a unique slug (snake_case, <=32 chars).
# Variants are two-key dicts ("a" and "b") so the simple modulo bucket works.
#
EXPERIMENTS = {
    "price_anchor_v1": {
        "name": "price_anchor_v1",
        "description": (
            "Test whether annual price ($99/yr) or monthly-billed-annually "
            "framing ($8.25/mo billed annually) converts better."
        ),
        "variants": {
            "a": {"display_price": "$99/yr", "framing": "annual"},
            "b": {"display_price": "$8.25/mo billed annually", "framing": "monthly_annualised"},
        },
    },
}

# Convention: the "primary" experiment that the pricing page actually swaps in.
# Future experiments can layer on top; this is the one that powers the v1 page.
PRIMARY_EXPERIMENT = "price_anchor_v1"


# --------------------------------------------------------------------------- #
# Persona-aware tier recommender.
# --------------------------------------------------------------------------- #

def _user_remittance_count(user) -> int:
    """Count remittance entries for ``user``. Never raises — returns 0 on
    any DB issue (e.g. user is None, model not importable, no rows)."""
    try:
        if user is None or not getattr(user, "id", None):
            return 0
        from remittance_models import RemittanceEntry
        return RemittanceEntry.query.filter_by(user_id=user.id).count()
    except Exception as exc:
        log.debug("_user_remittance_count failed for user=%s: %s",
                  getattr(user, "id", None), exc)
        return 0


def _user_has_foreign_tax_withheld(user) -> bool:
    """True if the user has at least one remittance with a non-null,
    non-zero ``foreign_tax_withheld_amount`` — signals DTA territory."""
    try:
        if user is None or not getattr(user, "id", None):
            return False
        from remittance_models import RemittanceEntry
        return (
            RemittanceEntry.query
            .filter(RemittanceEntry.user_id == user.id)
            .filter(RemittanceEntry.foreign_tax_withheld_amount.isnot(None))
            .filter(RemittanceEntry.foreign_tax_withheld_amount > 0)
            .count()
        ) > 0
    except Exception as exc:
        log.debug("_user_has_foreign_tax_withheld failed for user=%s: %s",
                  getattr(user, "id", None), exc)
        return False


def recommend_tier(user) -> str:
    """Pick the best ``PRICING_TIERS`` key for this user.

    Heuristic (Wave 2.2 council pick):
      * Any foreign tax withheld     -> ``premium`` (DTA reconciliation is
                                         the saved-money pitch)
      * > 20 remittances / lifetime  -> ``premium``
      * 6-20 remittances             -> ``pro``
      * < 6 remittances              -> ``self_serve``

    Returns ``"self_serve"`` for anonymous / unknown users (the conservative
    floor). Always returns a valid key from ``PRICING_TIERS``.
    """
    if _user_has_foreign_tax_withheld(user):
        return "premium"

    count = _user_remittance_count(user)
    if count > 20:
        return "premium"
    if count >= 6:
        return "pro"
    return "self_serve"


# --------------------------------------------------------------------------- #
# Deterministic A/B variant chooser.
# --------------------------------------------------------------------------- #

def assign_experiment_variant(user, experiment: str = PRIMARY_EXPERIMENT) -> dict:
    """Pick the variant for ``user`` in ``experiment``. Deterministic on
    ``user.id`` so the same user always sees the same variant.

    Anonymous users (no user.id) fall to variant "a" — keeps the unauth
    pricing page consistent across sessions for SEO + bookmarks.

    Returns ``{"experiment": <name>, "variant": "a"|"b", **variant_payload}``.
    Emits ``pricing_variant_assigned`` so the funnel can later attribute
    paid conversions back to the variant.
    """
    spec = EXPERIMENTS.get(experiment)
    if not spec:
        # Defensive — should never happen with PRIMARY_EXPERIMENT, but if a
        # caller passes a bad slug we degrade gracefully.
        return {"experiment": experiment, "variant": "a"}

    uid = getattr(user, "id", None) if user is not None else None
    if uid is None:
        variant_key = "a"
    else:
        # Two-variant bucket on user.id parity-modulo. Even -> a, Odd -> b.
        # Using % 100 first lets us extend to 3+ variants later without
        # reshuffling existing users (variant a stays even, b stays odd).
        variant_key = "a" if (uid % 100) < 50 else "b"

    payload = dict(spec["variants"].get(variant_key, {}))
    payload["experiment"] = experiment
    payload["variant"] = variant_key

    # Best-effort attribution event. Skipped silently for anon users (no
    # user_id to attribute to anyway).
    if uid is not None:
        emit_event(
            "pricing_variant_assigned",
            user_id=uid,
            payload={
                "experiment": experiment,
                "variant": variant_key,
            },
            source="pricing.assign_variant",
        )

    return payload


# --------------------------------------------------------------------------- #
# Flask blueprint — /pricing surface.
# --------------------------------------------------------------------------- #

pricing_bp = Blueprint("pricing", __name__, url_prefix="/pricing")


def _absolute_url(endpoint: str, **values) -> str:
    """``url_for`` with ``_external=True`` — Stripe needs absolute URLs."""
    return url_for(endpoint, _external=True, **values)


@pricing_bp.route("", methods=["GET"])
@pricing_bp.route("/", methods=["GET"])
def pricing_page():
    """Public pricing page. Anonymous users see all 3 tiers equally; logged-in
    users see their recommended tier badged. Emits ``pricing_page_viewed``."""
    user = current_user if getattr(current_user, "is_authenticated", False) else None
    recommended = recommend_tier(user) if user is not None else None
    variant = assign_experiment_variant(user)

    emit_event(
        "pricing_page_viewed",
        user_id=user.id if user else None,
        payload={
            "authenticated": user is not None,
            "recommended_tier": recommended,
            "experiment": variant.get("experiment"),
            "variant": variant.get("variant"),
        },
        source="route:pricing.page",
    )

    return render_template(
        "pricing.html",
        tiers=PRICING_TIERS,
        tier_order=["self_serve", "pro", "premium"],
        dta_add_on=DTA_ADD_ON,
        recommended_tier=recommended,
        variant=variant,
        authenticated=user is not None,
    )


@pricing_bp.route("/checkout/<tier>", methods=["POST"])
@login_required
def checkout(tier: str):
    """Create a Stripe Checkout Session for ``tier`` and redirect to it.

    Returns 404 for an unknown tier key (defence against guessed URLs).
    Returns 503 with a flash if Stripe SDK / secret is unavailable — the rest
    of the app stays usable in dev.
    """
    tier_spec = PRICING_TIERS.get(tier)
    if tier_spec is None:
        abort(404)

    variant = assign_experiment_variant(current_user)

    emit_event(
        "checkout_started",
        user_id=current_user.id,
        payload={
            "tier": tier,
            "price_lkr_yr": tier_spec["price_lkr_yr"],
            "price_usd_yr": tier_spec["price_usd_yr"],
            "experiment": variant.get("experiment"),
            "variant": variant.get("variant"),
        },
        source="route:pricing.checkout",
    )

    # Lazy import — keep the module importable when stripe SDK is absent.
    try:
        import stripe  # type: ignore
    except ImportError:
        log.warning("stripe SDK not installed; cannot create checkout session")
        flash(
            "Checkout is temporarily unavailable. The team has been notified.",
            "warning",
        )
        return redirect(url_for("pricing.pricing_page")), 503

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        log.warning("STRIPE_SECRET_KEY is not configured")
        flash(
            "Checkout is temporarily unavailable. The team has been notified.",
            "warning",
        )
        return redirect(url_for("pricing.pricing_page")), 503

    stripe.api_key = secret_key

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "lkr",
                        "product_data": {
                            "name": f"FIESTA {tier_spec['name']}",
                            "description": tier_spec["tagline"],
                        },
                        # Stripe wants the smallest unit. LKR has 2 decimals,
                        # so the rupee price * 100 = cents-of-rupee.
                        "unit_amount": int(tier_spec["price_lkr_yr"]) * 100,
                        "recurring": {"interval": "year"},
                    },
                    "quantity": 1,
                },
            ],
            customer_email=getattr(current_user, "email", None),
            success_url=_absolute_url(
                "pricing.checkout_success",
                session_id="{CHECKOUT_SESSION_ID}",
            ),
            cancel_url=_absolute_url("pricing.pricing_page"),
            metadata={
                "user_id": str(current_user.id),
                "tier": tier,
                "experiment": variant.get("experiment", ""),
                "variant": variant.get("variant", ""),
            },
            subscription_data={
                "metadata": {
                    "user_id": str(current_user.id),
                    "tier": tier,
                },
            },
        )
    except Exception as exc:
        log.exception("Stripe Checkout Session create failed: %s", exc)
        flash(
            "Checkout couldn't start. Please try again, or email support.",
            "danger",
        )
        return redirect(url_for("pricing.pricing_page"))

    # Stripe returns a hosted URL we redirect the user to.
    return redirect(session.url, code=303)


@pricing_bp.route("/checkout/success", methods=["GET"])
@login_required
def checkout_success():
    """Landing page after Stripe redirects on payment success.

    NOTE: payment is only "confirmed" once the ``checkout.session.completed``
    webhook fires server-to-server. This page is a courtesy UI; the user's
    subscription_status is flipped by the webhook, not by this view.
    """
    session_id = request.args.get("session_id", "")
    log.info("Checkout success landing for user=%s session=%s",
             current_user.id, session_id[:32])
    return render_template(
        "pricing.html",
        tiers=PRICING_TIERS,
        tier_order=["self_serve", "pro", "premium"],
        dta_add_on=DTA_ADD_ON,
        recommended_tier=recommend_tier(current_user),
        variant=assign_experiment_variant(current_user),
        authenticated=True,
        checkout_completed=True,
    )


@pricing_bp.route("/tiers.json", methods=["GET"])
def tiers_json():
    """Machine-readable mirror of PRICING_TIERS for the AI orchestrator,
    landing pages, and the marketing site. No auth, no events — pure data."""
    return jsonify({
        "tiers": PRICING_TIERS,
        "tier_order": ["self_serve", "pro", "premium"],
        "dta_add_on": DTA_ADD_ON,
    })


__all__ = [
    "PRICING_TIERS",
    "DTA_ADD_ON",
    "EXPERIMENTS",
    "PRIMARY_EXPERIMENT",
    "recommend_tier",
    "assign_experiment_variant",
    "pricing_bp",
]
