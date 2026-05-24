"""
referral_routes.py - Tier D4 / A3: One-sided referral loop routes.

Three surfaces:

  * ``GET  /referrals``              (login_required) - referrer dashboard.
                                      Shows the user's code, uses_count,
                                      pending vs paid redemptions, credits
                                      applied.
  * ``POST /api/referrals/generate`` (login_required) - idempotent code
                                      generator for the current user. Returns
                                      JSON {code, share_url}.
  * ``GET  /r/<code>``               (anon) - landing page. Drops a
                                      ``referral_code`` cookie (30-day
                                      expiry) and redirects to the signup
                                      form.

Cookie capture is later read by the signup flow (fiesta.signup.routes) via
the ``capture_referral_cookie_on_signup`` helper which creates a
``ReferralRedemption`` row.

CSRF: ``/api/referrals/generate`` is POST + login_required. We exempt it
from CSRF because callers are first-party JS that don't carry a token
(matches the pattern used elsewhere in the codebase). For a same-origin
endpoint that requires a valid session cookie the CSRF risk is minimal -
worst-case attacker can mint a referral code for a logged-in victim,
which has no security impact.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from flask import (
    Blueprint, request, redirect, url_for, jsonify, render_template,
    make_response, abort, current_app,
)
from flask_login import login_required, current_user

from events import emit as emit_analytics_event
from referral_models import (
    get_or_create_code_for_user, lookup_redeemable_code,
    record_signup_redemption, ReferralCode, ReferralRedemption,
    COOKIE_NAME, COOKIE_MAX_AGE_SECONDS, REFERRAL_DISCOUNT_PERCENT,
)

log = logging.getLogger(__name__)


referral_bp = Blueprint("referrals", __name__, url_prefix="")


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #

def _share_url_for(code: str) -> str:
    """Absolute URL for sharing. Falls back to request.host_url if app config
    REFERRAL_BASE_URL is not set."""
    base = current_app.config.get("REFERRAL_BASE_URL") or request.host_url
    if not base.endswith("/"):
        base += "/"
    return f"{base}r/{code}"


def _redemption_rows_for_referrer(user_id: int):
    """Return (pending, paid, credited) lists of ReferralRedemption rows for
    a referrer (the user who owns the code)."""
    if ReferralCode is None or ReferralRedemption is None:
        return [], [], []
    code_row = ReferralCode.query.filter_by(user_id=user_id).first()
    if code_row is None:
        return [], [], []
    all_rows = (
        ReferralRedemption.query
        .filter_by(code_id=code_row.id)
        .order_by(ReferralRedemption.redeemed_at.desc())
        .all()
    )
    pending = [r for r in all_rows if r.paid_at is None]
    paid = [r for r in all_rows if r.paid_at is not None]
    credited = [r for r in all_rows if r.referrer_credit_applied_at is not None]
    return pending, paid, credited


# --------------------------------------------------------------------------- #
# Routes.
# --------------------------------------------------------------------------- #

@referral_bp.route("/referrals", methods=["GET"])
@login_required
def referral_dashboard():
    """Referrer dashboard. Auto-creates a code on first visit (so the user
    can share it immediately without a separate "create code" click)."""
    code_row = get_or_create_code_for_user(current_user.id)
    if code_row is None:
        log.warning(
            "referral_dashboard: failed to provision code for user=%s",
            current_user.id,
        )
        # Render the dashboard with a friendly fallback rather than 500ing.
        return render_template(
            "referrals/dashboard.html",
            code=None, share_url=None,
            uses_count=0, max_uses=0, is_redeemable=False,
            pending=[], paid=[], credited=[],
            discount_percent=REFERRAL_DISCOUNT_PERCENT,
            error="We couldn't generate your referral code. Try refreshing.",
        )

    pending, paid, credited = _redemption_rows_for_referrer(current_user.id)
    return render_template(
        "referrals/dashboard.html",
        code=code_row.code,
        share_url=_share_url_for(code_row.code),
        uses_count=code_row.uses_count,
        max_uses=code_row.max_uses,
        is_redeemable=code_row.is_redeemable,
        pending=pending,
        paid=paid,
        credited=credited,
        discount_percent=REFERRAL_DISCOUNT_PERCENT,
        error=None,
    )


@referral_bp.route("/api/referrals/generate", methods=["POST"])
@login_required
def api_generate_code():
    """Idempotent: returns the user's existing code on every call. The name
    ``generate`` is preserved for the API contract; the underlying helper
    always reuses an existing code if one exists."""
    code_row = get_or_create_code_for_user(current_user.id)
    if code_row is None:
        return jsonify({
            "ok": False,
            "error": "failed_to_generate",
        }), 500
    return jsonify({
        "ok": True,
        "code": code_row.code,
        "share_url": _share_url_for(code_row.code),
        "uses_count": code_row.uses_count,
        "max_uses": code_row.max_uses,
        "is_redeemable": code_row.is_redeemable,
    }), 200


@referral_bp.route("/r/<code>", methods=["GET"])
def referral_landing(code: str):
    """Anonymous landing page. Drops the referral_code cookie + renders a
    welcome page with a CTA to signup. We don't 404 on bad codes - we
    render the page with an invalid-code notice + still send users to
    signup so we don't lose the funnel."""
    code_row = lookup_redeemable_code(code)
    valid = code_row is not None

    referrer_email = None
    if valid and code_row is not None:
        try:
            from models import User
            u = User.query.get(code_row.user_id)
            if u is not None:
                # Show only the masked email for the referee's reassurance
                # ("invited by m***@gmail.com") - don't leak the full address.
                referrer_email = _mask_email(u.email or "")
        except Exception:
            referrer_email = None

    signup_qs = urlencode({"ref": code}) if valid else ""
    signup_url = f"/signup?{signup_qs}" if signup_qs else "/signup"

    resp = make_response(render_template(
        "referrals/landing.html",
        valid=valid,
        code=code,
        referrer_email=referrer_email,
        signup_url=signup_url,
        discount_percent=REFERRAL_DISCOUNT_PERCENT,
    ))
    if valid:
        # Set httponly cookie. NOT secure-only (dev http) - the signup flow
        # uses the same cookie regardless.
        resp.set_cookie(
            COOKIE_NAME, code,
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="Lax",
            path="/",
        )

    emit_analytics_event(
        "referral_landing_visited",
        user_id=None,
        payload={"code": code, "valid": valid},
        source="route:referrals.landing",
    )
    return resp


def _mask_email(email: str) -> str:
    """Return ``m***@gmail.com`` form. Empty string in -> empty string out."""
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local:
        return f"@{domain}"
    return f"{local[0]}***@{domain}"


# --------------------------------------------------------------------------- #
# Signup hook + Stripe webhook hook - called from elsewhere.
# --------------------------------------------------------------------------- #

def capture_referral_cookie_on_signup(referee_user_id: int) -> object:
    """Called from fiesta.signup.routes.signup_submit() right after a new
    User row is committed. Reads the referral_code cookie (if present) and
    creates a ReferralRedemption row.

    Never raises - signup must not fail because of a referral hiccup.
    Returns the redemption row or None.
    """
    try:
        code = request.cookies.get(COOKIE_NAME)
        if not code:
            return None
        row = record_signup_redemption(code, referee_user_id)
        if row is not None:
            emit_analytics_event(
                "referral_redemption_signup",
                user_id=referee_user_id,
                payload={
                    "code": code,
                    "redemption_id": row.id,
                },
                source="signal:referral.capture_on_signup",
            )
        return row
    except Exception as exc:
        log.warning(
            "capture_referral_cookie_on_signup: failed for referee=%s: %s",
            referee_user_id, exc,
        )
        return None


# --------------------------------------------------------------------------- #
# Registration helper.
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register the referrals blueprint + CSRF-exempt the API endpoint.

    Idempotent. Safe to call from main.py at orchestrator wire-time.
    """
    if "referrals" in app.blueprints:
        return
    app.register_blueprint(referral_bp)

    # Exempt the API + landing endpoints from CSRF. /api/referrals/generate
    # is first-party JS without a token; /r/<code> is GET-only so CSRF
    # doesn't apply, but exempting the blueprint wholesale is simpler than
    # naming each route.
    try:
        from app import csrf
        try:
            csrf.exempt(referral_bp)
        except Exception:
            csrf.exempt(api_generate_code)
        log.info("referrals blueprint CSRF-exempted")
    except Exception as exc:
        log.warning(
            "Could not CSRF-exempt referrals (%s); /api/referrals/generate may 400",
            exc,
        )

    log.info(
        "Referral routes registered "
        "(/referrals, /api/referrals/generate, /r/<code>)"
    )


__all__ = [
    "referral_bp",
    "register_routes",
    "referral_dashboard",
    "api_generate_code",
    "referral_landing",
    "capture_referral_cookie_on_signup",
]
