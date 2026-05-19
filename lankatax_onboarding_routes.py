"""
Lanka.tax 1-click onboarding routes — Wave 3.3 (2026-05-18).

The deep link Lanka.tax sends in cross-sell emails lands here. We verify a
signed token, log the user in via Flask-Login, set the foreign-income
persona if missing, set the acquisition source on CustomerProfile, mark the
matching LankataxOutreach row as opened, emit a lankatax_onboarding_started
event, and redirect to the remittance dashboard.

Signed-token scheme: ``itsdangerous.URLSafeSerializer`` keyed on
``app.secret_key`` (FIESTA uses ``URLSafeTimedSerializer`` for email
verification — we deliberately use the un-timed variant here because the
Lanka.tax→FIESTA cross-sell link is intentionally long-lived. The orchestrator
can swap to URLSafeTimedSerializer with a max_age if the council later
demands expiry).

generate_token() / verify_token() are exposed at module level so
lankatax_crosssell.run_campaign() can build the deep link without importing
the blueprint.
"""
import logging
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, current_app, abort,
)
from flask_login import login_user
from itsdangerous import URLSafeSerializer, BadSignature

from app import db
from events import emit as emit_event

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Token signing
# --------------------------------------------------------------------------- #

# Salt is fixed (not per-token like email verification) — the value is a
# (user_id, campaign_key) tuple that we want to validate against ANY token
# previously generated for that pair, not a one-shot nonce.
_TOKEN_SALT = "lankatax-onboarding-v1"


def _serializer():
    """Build the serializer lazily so we pick up the live SECRET_KEY at call
    time (tests sometimes swap the app config between fixtures)."""
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt=_TOKEN_SALT)


def generate_token(user_id: int, campaign_key: str) -> str:
    """Sign and return a token encoding (user_id, campaign_key).

    Called from lankatax_crosssell.run_campaign() when composing the deep
    link for each recipient. The token is the only piece the recipient sees
    that proves they're the intended audience for the deep link.
    """
    return _serializer().dumps({"user_id": int(user_id), "campaign_key": str(campaign_key)})


def verify_token(token: str):
    """Return the decoded payload dict, or None on invalid/tampered token.

    Never raises. Callers branch on truthiness.
    """
    try:
        data = _serializer().loads(token)
        if not isinstance(data, dict):
            return None
        if "user_id" not in data or "campaign_key" not in data:
            return None
        return data
    except BadSignature:
        return None
    except Exception as exc:
        log.warning("verify_token: unexpected error: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Blueprint
# --------------------------------------------------------------------------- #

onboarding_bp = Blueprint(
    "lankatax_onboarding",
    __name__,
    url_prefix="/onboarding",
)


@onboarding_bp.route("/lankatax", methods=["GET"])
def lankatax_onboarding():
    """Verify the signed token, log the user in, set attribution + persona,
    record the open, redirect to the remittance dashboard.

    Query string:
      token        signed payload encoding {user_id, campaign_key}
      utm_source   typically 'lankatax' (recorded as acquisition_source)
      utm_campaign campaign key (used as a fallback if token decode fails
                   on campaign_key, though that shouldn't happen)
    """
    token = request.args.get("token", "")
    utm_source = (request.args.get("utm_source") or "").strip()[:64]
    utm_campaign = (request.args.get("utm_campaign") or "").strip()[:64]

    if not token:
        return render_template("onboarding/lankatax_invalid_link.html"), 200

    data = verify_token(token)
    if not data:
        log.info("lankatax_onboarding: bad token (utm=%s)", utm_campaign)
        return render_template("onboarding/lankatax_invalid_link.html"), 200

    user_id = data.get("user_id")
    campaign_key = data.get("campaign_key") or utm_campaign or "unknown"

    # Resolve user
    try:
        from models import User
        user = User.query.get(int(user_id)) if user_id is not None else None
    except Exception as exc:
        log.warning("lankatax_onboarding: User lookup failed for %s: %s", user_id, exc)
        user = None

    if user is None:
        log.info("lankatax_onboarding: token decoded but user %s missing", user_id)
        return render_template("onboarding/lankatax_invalid_link.html"), 200

    # Log the user in (Flask-Login). The session cookie is set on the
    # response we return below.
    try:
        login_user(user, remember=True)
    except Exception as exc:
        # If login_user fails, surface the invalid-link page rather than 500.
        log.warning("lankatax_onboarding: login_user failed for %s: %s", user.id, exc)
        return render_template("onboarding/lankatax_invalid_link.html"), 200

    # Persona — set 'sl_foreign_income' if currently null. Lanka.tax users
    # are all foreign-income earners by definition; this is the correct
    # default. We do NOT overwrite an existing persona (user may have
    # already self-selected a different one in the meantime).
    persona_set_now = False
    try:
        # Use expire to defeat any stale identity-map state — the user may
        # have been mutated by another session between User.query.get above
        # and this branch, and we want the freshest view before deciding.
        db.session.expire(user, ["persona"])
        if not getattr(user, "persona", None):
            user.persona = "sl_foreign_income"
            persona_set_now = True
            db.session.commit()
    except Exception as exc:
        log.warning("lankatax_onboarding: persona set failed for %s: %s", user.id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass

    # Attribution — write 'lankatax' (or the supplied utm_source) onto the
    # CustomerProfile row. Get-or-create the profile if it doesn't exist;
    # acquisition_source is the one attribute the brain leaves untouched on
    # recompute (per ai_crm.recompute_profile docstring).
    try:
        from ai_crm import CustomerProfile
        profile = (
            CustomerProfile.query
                          .filter(CustomerProfile.user_id == user.id)
                          .first()
        )
        if profile is None:
            profile = CustomerProfile(user_id=user.id, first_seen_at=datetime.utcnow())
            db.session.add(profile)
        # Only set if currently null — preserve the original attribution if
        # the user landed via Lanka.tax once and is now coming through a
        # different surface.
        if not profile.acquisition_source:
            profile.acquisition_source = utm_source or "lankatax"
        db.session.commit()
    except Exception as exc:
        log.warning(
            "lankatax_onboarding: acquisition_source set failed for %s: %s",
            user.id, exc,
        )
        try:
            db.session.rollback()
        except Exception:
            pass

    # Mark the LankataxOutreach row as opened. We pick the MOST RECENT
    # matching row (user_id + campaign_key) that hasn't already been opened —
    # if a user clicks the link in multiple sends of the same campaign,
    # opened_at flips on the latest unopened one.
    try:
        from lankatax_models import LankataxOutreach
        from sqlalchemy import desc
        outreach = (
            LankataxOutreach.query
                            .filter(LankataxOutreach.user_id == user.id,
                                    LankataxOutreach.campaign_key == campaign_key,
                                    LankataxOutreach.opened_at.is_(None))
                            .order_by(desc(LankataxOutreach.sent_at))
                            .first()
        )
        if outreach is not None:
            now = datetime.utcnow()
            outreach.opened_at = now
            outreach.clicked_at = now      # a deep-link click implies both
            db.session.commit()
    except Exception as exc:
        log.warning(
            "lankatax_onboarding: outreach mark-opened failed for user=%s campaign=%s: %s",
            user.id, campaign_key, exc,
        )
        try:
            db.session.rollback()
        except Exception:
            pass

    # Emit the event for downstream funnel attribution.
    emit_event(
        "lankatax_onboarding_started",
        user_id=user.id,
        payload={
            "campaign_key": campaign_key,
            "utm_source": utm_source or "lankatax",
            "persona_set_now": persona_set_now,
        },
        source="route:lankatax_onboarding",
    )

    # Drop them on the remittance dashboard — their FIESTA persona home.
    return redirect(url_for("remittance.dashboard"))


# --------------------------------------------------------------------------- #
# Registration helper
# --------------------------------------------------------------------------- #

def register_routes(app):
    """Register the Lanka.tax onboarding blueprint on `app`.

    Idempotent: re-registration is a no-op (Flask raises on duplicate names,
    so we guard with an explicit check — same pattern Wave 2.x routes use).
    """
    if "lankatax_onboarding" in app.blueprints:
        return
    app.register_blueprint(onboarding_bp)


__all__ = [
    "onboarding_bp",
    "register_routes",
    "generate_token",
    "verify_token",
]
