"""
Admin views for the Customer Brain (AI CRM) — Wave 2.3.

Per-user 360 view at /admin/customer/<int:user_id>:
  * The brain's current opinion of the user (CustomerProfile)
  * The full timeline (events + remittances + audit_log, merged)
  * A "Recompute now" admin action for manual refresh while iterating

Admin guard is INLINE abort(403) rather than the @admin_required decorator,
per spec — we want a hard 403 response for non-admins, not a redirect-to-index.
This matches the strictness of remittance_routes._user_can_read_entry().
"""
import logging

from flask import Blueprint, render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

log = logging.getLogger(__name__)


customer_brain_bp = Blueprint(
    "customer_brain", __name__, url_prefix="/admin/customer"
)


def _require_admin():
    """Inline gate — abort(403) for any non-admin, including unauthenticated.

    Returns None on success (caller proceeds). Aborts the request on failure.
    """
    if not current_user.is_authenticated:
        abort(403)
    # User.is_admin() exists; falling back to .role for any subclass that
    # forgot the helper is defence-in-depth.
    if not (getattr(current_user, "is_admin", lambda: False)() or
            getattr(current_user, "role", "user") == "admin"):
        abort(403)


@customer_brain_bp.route("/<int:user_id>", methods=["GET"])
@login_required
def view(user_id):
    """Render the per-user 360 page."""
    _require_admin()

    # Local import keeps the customer_brain module load cheap and avoids any
    # accidental circular-import surface (ai_crm imports models; models doesn't
    # import ai_crm — defence-in-depth).
    from ai_crm import (
        CustomerProfile,
        aggregate_user_timeline,
        recompute_profile,
    )
    from models import User

    user = User.query.get_or_404(user_id)

    profile = (
        CustomerProfile.query
                       .filter(CustomerProfile.user_id == user_id)
                       .first()
    )
    if profile is None:
        # First visit ever for this user — compute on demand. If the recompute
        # itself fails we still render the page with profile=None so the admin
        # can see the timeline and hit "Recompute" manually.
        profile = recompute_profile(user_id)

    timeline = aggregate_user_timeline(user_id)

    return render_template(
        "admin/customer_profile.html",
        user=user,
        profile=profile,
        timeline=timeline,
    )


@customer_brain_bp.route("/<int:user_id>/recompute", methods=["POST"])
@login_required
def recompute(user_id):
    """Force a brain recompute for this user. Useful while iterating on the
    risk/NBA heuristics. Redirects back to the view page."""
    _require_admin()

    from ai_crm import recompute_profile
    from models import User

    user = User.query.get_or_404(user_id)
    profile = recompute_profile(user_id)
    if profile is None:
        flash(f"Recompute failed for user {user.email}. Check server logs.", "danger")
    else:
        flash(
            f"Recomputed: stage={profile.lifecycle_stage}, "
            f"risk={profile.risk_score}, NBA={profile.next_best_action}",
            "success",
        )

    return redirect(url_for("customer_brain.view", user_id=user_id))


def register_routes(app):
    """Register the customer_brain blueprint on the Flask app.

    Called from main.py — same pattern as remittance_routes.register_routes.
    """
    app.register_blueprint(customer_brain_bp)
    log.info("Customer Brain (AI CRM) routes registered at /admin/customer/*")
