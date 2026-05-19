"""
Pricing Engine — FIESTA v4.1 monetization for the Sri Lankan
software-engineer / designer / consultant persona (P2 Business Owner cohort,
ages 22-35, price-averse, never filed a tax return before).

Wave 2 dispatch realignment (2026-05-20). Replaces the Wave 2.2 three-tier
$99/$199/$349 foreign-income schema with the v4.1 canonical schema agreed in
the FIESTA council brief (core_concept.pricing_v4_1):

  * Free Trial   — Rs 0, 30 days (triage + manual roster + agreement preview
                   + tax-result preview)
  * Self-File    — Rs 2,500 / year (Vision-clone OCR + AI Fiesta Guide chat
                   + signed agreement PDFs + monthly cadence + year-end pack
                   + IRD-portal walkthrough)
  * Auto-File    — Rs 5,000 / year (Self-File + automation_runner submission
                   + acknowledgement tracker + quarterly scheduler) — DEFERRED
                   to v1.1; surfaced internally but NOT customer-facing yet.
  * Consultant   — Rs 5,000 / 30 min, one-off (Google Calendar Appointment
   Booking         Schedule + Google Meet auto-link + SendGrid prep brief).
                   Available to all signed-up tiers including Free Trial.

Stripe architecture (council Opus pick, council_brief line 510):
  * Single Stripe-UK account.
  * ``mode="payment"`` (one-time per Year of Assessment) — pricing v4.1 is
    annual but NOT recurring; users re-engage each YoA.
  * Multi-currency presentment (LKR primary + USD parenthetical via
    Stripe's localised display; the unit_amount is LKR cents).

Design intent
-------------
1. ONE source of truth for tier definitions — ``PRICING_TIERS`` — referenced
   by the public page, the Stripe checkout creator, the recommender, the
   email templates, and the tests. Adding a tier means adding a key here
   and nothing else.

2. ``PRICING_VERSION`` constant pinned at the top so future refactors can
   grep for the active schema version. Bump on every breaking change.

3. ``AUTO_FILE_ENABLED`` feature flag. Auto-File is in the schema (the
   value is canonical) but hidden from the customer-facing pricing page
   until v1.1 ships the automation_runner SL adapter end-to-end. When the
   flag flips, Auto-File appears as a tier card without any other code
   change.

4. Persona-aware ``recommend_tier(user)`` updated for v4.1 semantics:
   freshly-signed-up users see ``free_trial`` (no card required), users
   with a paid history see ``self_file``, and (once enabled) heavy users
   see ``auto_file``.

5. A/B variant chooser (``assign_experiment_variant``) preserved verbatim
   from Wave 2.2 — the variant infrastructure is orthogonal to which
   tiers exist. The current price-anchor experiment is rewritten in LKR.

6. Stripe is imported lazily inside the checkout handler so the module
   stays importable in test/dev environments without the SDK installed.

7. Every revenue-relevant action emits an Event row via ``events.emit`` so
   the Wave 2 funnel dashboard reads pricing_page_viewed ->
   checkout_started -> checkout_completed without bespoke instrumentation.
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
# Version pin — bump on every breaking pricing-schema change.
# --------------------------------------------------------------------------- #
PRICING_VERSION = "v4.1"


# --------------------------------------------------------------------------- #
# Feature flag — Auto-File is v1.1; hide from customer surface until ready.
# Flip to True (or read from env) when automation_runner SL adapter is live.
# --------------------------------------------------------------------------- #
AUTO_FILE_ENABLED = os.environ.get("FIESTA_AUTO_FILE_ENABLED", "0").lower() in (
    "1", "true", "yes", "on",
)


# --------------------------------------------------------------------------- #
# Tier definitions — single source of truth (v4.1 canonical).
# --------------------------------------------------------------------------- #
#
# Prices are in LKR (the only currency Stripe-UK presents to SL buyers per
# the council architecture decision). USD is shown parenthetically by the
# template for foreign-income earners, computed at runtime from a CBSL rate
# — NOT pinned in this file, so we never drift from the live FX.
#
# Term semantics:
#   - free_trial:    30 days, no card. ``term_days`` is the duration.
#   - self_file:     1 Year of Assessment. One-time payment, not recurring.
#   - auto_file:     1 Year of Assessment. One-time payment, not recurring.
#                    HIDDEN from /pricing while AUTO_FILE_ENABLED is False.
#   - consultant_booking is a SIBLING, not a tier — see CONSULTANT_BOOKING.
#
PRICING_TIERS = {
    "free_trial": {
        "key": "free_trial",
        "name": "Free Trial",
        "tagline": "Try FIESTA for 30 days. No card required.",
        "price_lkr_yr": 0,
        "term": "30 days",
        "term_days": 30,
        "features": [
            "Triage of your remittances and tax position",
            "Manual roster of income sources",
            "Agreement preview (watermarked)",
            "Tax-result preview (LKR amount + bracket)",
            "Upgrade to Self-File anytime to lock in your return",
        ],
        "cta": "Start free trial",
        "best_for": "First-time filers who want to see FIESTA before paying.",
        "available": True,
    },
    "self_file": {
        "key": "self_file",
        "name": "Self-File",
        "tagline": "File your own return with FIESTA's AI alongside you.",
        "price_lkr_yr": 2500,
        "term": "per Year of Assessment",
        "features": [
            "Vision-clone OCR for T10 + bank statements + payslips",
            "AI Fiesta Guide chat — answers your tax questions",
            "Signed agreement PDFs (consultant + roster + filing)",
            "Monthly cadence reminders (so you never miss a quarter)",
            "Year-end pack: ready-to-lodge return + supporting docs",
            "IRD-portal walkthrough video for first-time filers",
        ],
        "cta": "Choose Self-File",
        "best_for": "Software engineers, designers, consultants filing their first SL return.",
        "available": True,
    },
    "auto_file": {
        "key": "auto_file",
        "name": "Auto-File",
        "tagline": "FIESTA submits to the IRD portal for you. Coming in v1.1.",
        "price_lkr_yr": 5000,
        "term": "per Year of Assessment",
        "features": [
            "Everything in Self-File",
            "Automated submission via automation_runner",
            "IRD acknowledgement tracker (you get a copy in your inbox)",
            "Quarterly scheduler — quarterly tax instalments handled",
            "Available v1.1 — Self-File first, upgrade later",
        ],
        "cta": "Coming in v1.1",
        "best_for": "Filers who want hands-off compliance. Currently disabled.",
        "available": AUTO_FILE_ENABLED,
        "coming_soon": True,
        "release_target": "v1.1",
    },
}


# --------------------------------------------------------------------------- #
# Consultant booking — a SIBLING product, not a tier (one-off, not annual).
# Available to every signed-up user including Free Trial. Fulfilled via the
# Google Calendar Appointment Schedule + Meet auto-link + SendGrid prep brief.
# --------------------------------------------------------------------------- #
CONSULTANT_BOOKING = {
    "key": "consultant_booking",
    "name": "Consultant Booking",
    "price_lkr": 5000,
    "term": "one-off / 30 min",
    "description": (
        "30-minute live tax consultation with a Lanka.tax tax officer. "
        "One-off charge — available to all signed-up tiers including Free Trial. "
        "Fulfilled via Google Calendar Appointment Schedule + Google Meet."
    ),
    "calendar_url": "https://calendar.app.google/upp97vgtE7oYVdzn9",
    "available_to": ("free_trial", "self_file", "auto_file"),
}


# Back-compat alias — the Wave 2.2 template referenced ``dta_add_on``. The
# DTA reconciler is folded into Self-File's OCR + Guide-chat surface in
# v4.1 and is no longer a separately-priced add-on. Keep the symbol so any
# stragglers importing it get the consultant booking object instead of a
# crash; remove this alias once nothing references it (search for
# ``DTA_ADD_ON`` and ``dta_add_on``).
DTA_ADD_ON = CONSULTANT_BOOKING


# --------------------------------------------------------------------------- #
# A/B experiments — module-level so consumers (Wave 2 funnel dashboard) can
# enumerate the variants without spelunking the route handler.
# --------------------------------------------------------------------------- #
#
# v4.1: anchor experiment rewritten in LKR. Variant A is the headline annual
# price ("Rs 2,500/year"); Variant B is the monthly-framing equivalent
# ("Rs 209/mo, billed annually") which testing in foreign markets suggests
# nudges price-averse buyers but may feel dishonest for a one-off product —
# the experiment tells us which framing this cohort actually clicks.
#
EXPERIMENTS = {
    "price_anchor_v1": {
        "name": "price_anchor_v1",
        "description": (
            "Test whether annual price (Rs 2,500/year) or monthly-framed "
            "equivalent (Rs 209/mo billed annually) converts better for the "
            "P2 Business Owner cohort."
        ),
        "variants": {
            "a": {"display_price": "Rs 2,500/year", "framing": "annual"},
            "b": {"display_price": "Rs 209/mo billed annually", "framing": "monthly_annualised"},
        },
    },
}

# Convention: the "primary" experiment that the pricing page actually swaps in.
# Future experiments can layer on top; this is the one that powers the v1 page.
PRIMARY_EXPERIMENT = "price_anchor_v1"


# --------------------------------------------------------------------------- #
# Persona-aware tier recommender — v4.1 semantics.
# --------------------------------------------------------------------------- #

def _user_has_paid_history(user) -> bool:
    """True if the user has paid Lanka.tax before (returning filer).
    Heuristic: any non-trial subscription_status, OR any prior remittance
    entry — both signal "this isn't their first time"."""
    try:
        if user is None or not getattr(user, "id", None):
            return False
        status = getattr(user, "subscription_status", None)
        if status and status not in ("", "free_trial", "free"):
            return True
        from remittance_models import RemittanceEntry
        return (
            RemittanceEntry.query
            .filter(RemittanceEntry.user_id == user.id)
            .count()
        ) > 0
    except Exception as exc:
        log.debug("_user_has_paid_history failed for user=%s: %s",
                  getattr(user, "id", None), exc)
        return False


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


def recommend_tier(user) -> str:
    """Pick the best ``PRICING_TIERS`` key for this user.

    v4.1 heuristic:
      * Anonymous / brand-new account -> ``free_trial`` (no card friction)
      * Paid history OR > 0 remittances -> ``self_file`` (they're serious)
      * Heavy users (> 20 remittances) AND ``AUTO_FILE_ENABLED`` -> ``auto_file``

    Always returns a valid key from ``PRICING_TIERS``. Never returns
    ``auto_file`` while the feature flag is off — falls back to
    ``self_file`` instead.
    """
    if user is None or not getattr(user, "id", None):
        return "free_trial"

    count = _user_remittance_count(user)
    paid = _user_has_paid_history(user)

    if AUTO_FILE_ENABLED and count > 20:
        return "auto_file"
    if paid or count > 0:
        return "self_file"
    return "free_trial"


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
# Helpers — surface filtering (what does /pricing actually show?)
# --------------------------------------------------------------------------- #

def _customer_facing_tier_order() -> list:
    """Tier keys to show on the public pricing page, in display order.

    Free Trial first (lowest friction), Self-File second (the v1.0
    revenue tier), Auto-File third only if the v1.1 flag is on.
    """
    order = ["free_trial", "self_file"]
    if AUTO_FILE_ENABLED:
        order.append("auto_file")
    return order


def _customer_facing_tiers() -> dict:
    """Subset of ``PRICING_TIERS`` to render for customers. Mirrors
    ``_customer_facing_tier_order`` so the dict + order list stay in sync."""
    return {k: PRICING_TIERS[k] for k in _customer_facing_tier_order()}


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
    """Public pricing page. Anonymous users see Free Trial + Self-File
    equally; logged-in users see their recommended tier badged. Emits
    ``pricing_page_viewed``."""
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
            "pricing_version": PRICING_VERSION,
        },
        source="route:pricing.page",
    )

    return render_template(
        "pricing.html",
        tiers=_customer_facing_tiers(),
        tier_order=_customer_facing_tier_order(),
        consultant_booking=CONSULTANT_BOOKING,
        # Back-compat alias for any template still referencing dta_add_on:
        dta_add_on=CONSULTANT_BOOKING,
        recommended_tier=recommended,
        variant=variant,
        authenticated=user is not None,
        pricing_version=PRICING_VERSION,
        auto_file_enabled=AUTO_FILE_ENABLED,
    )


@pricing_bp.route("/checkout/<tier>", methods=["POST"])
@login_required
def checkout(tier: str):
    """Create a Stripe Checkout Session for ``tier`` and redirect to it.

    Returns 404 for an unknown tier key (defence against guessed URLs).
    Returns 503 with a flash if Stripe SDK / secret is unavailable — the rest
    of the app stays usable in dev.

    v4.1: mode=payment (one-time per YoA, not subscription); LKR currency;
    Auto-File checkout is BLOCKED while AUTO_FILE_ENABLED is False.
    """
    tier_spec = PRICING_TIERS.get(tier)
    if tier_spec is None:
        abort(404)

    # Block checkout for unavailable tiers (Auto-File pre-v1.1) and for
    # Free Trial (no payment required — that route belongs to onboarding,
    # not /pricing/checkout).
    if not tier_spec.get("available", True):
        flash(
            f"{tier_spec['name']} isn't available yet. Start with Self-File and "
            f"we'll upgrade you the moment it launches.",
            "info",
        )
        return redirect(url_for("pricing.pricing_page")), 303
    if tier_spec.get("price_lkr_yr", 0) == 0:
        flash(
            "Free Trial starts from sign-up — no checkout needed. "
            "If you're already signed up, head to your dashboard.",
            "info",
        )
        return redirect(url_for("pricing.pricing_page")), 303

    variant = assign_experiment_variant(current_user)

    emit_event(
        "checkout_started",
        user_id=current_user.id,
        payload={
            "tier": tier,
            "price_lkr_yr": tier_spec["price_lkr_yr"],
            "experiment": variant.get("experiment"),
            "variant": variant.get("variant"),
            "pricing_version": PRICING_VERSION,
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
            # v4.1: one-time payment per Year of Assessment, NOT a recurring
            # subscription. Users re-engage each YoA.
            mode="payment",
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
                "pricing_version": PRICING_VERSION,
                "experiment": variant.get("experiment", ""),
                "variant": variant.get("variant", ""),
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
        tiers=_customer_facing_tiers(),
        tier_order=_customer_facing_tier_order(),
        consultant_booking=CONSULTANT_BOOKING,
        dta_add_on=CONSULTANT_BOOKING,
        recommended_tier=recommend_tier(current_user),
        variant=assign_experiment_variant(current_user),
        authenticated=True,
        checkout_completed=True,
        pricing_version=PRICING_VERSION,
        auto_file_enabled=AUTO_FILE_ENABLED,
    )


@pricing_bp.route("/tiers.json", methods=["GET"])
def tiers_json():
    """Machine-readable mirror of PRICING_TIERS for the AI orchestrator,
    landing pages, and the marketing site. No auth, no events — pure data.

    Includes ALL tiers (visible + hidden) so internal consumers can reason
    about the full schema; downstream UIs should filter on ``available``.
    """
    return jsonify({
        "pricing_version": PRICING_VERSION,
        "auto_file_enabled": AUTO_FILE_ENABLED,
        "tiers": PRICING_TIERS,
        "tier_order_customer_facing": _customer_facing_tier_order(),
        "tier_order_all": ["free_trial", "self_file", "auto_file"],
        "consultant_booking": CONSULTANT_BOOKING,
        # Back-compat alias:
        "dta_add_on": CONSULTANT_BOOKING,
    })


__all__ = [
    "PRICING_VERSION",
    "AUTO_FILE_ENABLED",
    "PRICING_TIERS",
    "CONSULTANT_BOOKING",
    "DTA_ADD_ON",  # back-compat alias for CONSULTANT_BOOKING
    "EXPERIMENTS",
    "PRIMARY_EXPERIMENT",
    "recommend_tier",
    "assign_experiment_variant",
    "pricing_bp",
]
