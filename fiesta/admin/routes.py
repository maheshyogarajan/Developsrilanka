"""
S15 Admin Users list — Wave 6 (2026-05-20).

Route: ``GET /admin/fie/users`` — admin-only paginated table of every FIESTA
user with persona, subscription tier + status, Stripe customer id (cached,
no live API call), and an "audit health" badge.

Spec ambiguities resolved
-------------------------
1. ``Persona (foreign / mixed / local from S1 triage)`` -- S1 triage isn't a
   shipped screen yet; we display whatever is in ``User.persona`` (current
   values in prod: ``self``, ``sl_foreign_income``, ``None``). The column
   header reads "Persona" so it makes sense regardless of the eventual
   triage taxonomy.

2. ``Subscription tier (Trial / Self-File / Auto-File)`` -- two stores
   carry tier info:
     a. ``User.subscription_status``  — legacy free-text ('free_trial',
        'self_file', 'auto_file', 'past_due', 'cancelled').
     b. ``paywall_subscription``       — Wave 2 X1 paid unlocks.
   We surface (a) as the *status* and derive *tier* from (a) too — the
   paywall_subscription table is the audit source but the legacy column is
   the operational reader. Anything that needs a live Stripe call has a
   ``TODO(main-context):`` marker.

3. ``Last login`` -- ``User`` has no ``last_login`` column; using
   ``User.created_at`` for "Created at". Last login displayed as "—" with a
   ``TODO(main-context):`` to wire up session/login tracking in a follow-up.

4. ``Audit health badge`` (red if ANY):
     a. ``tos_accepted_version`` is NULL or empty
     b. ``tos_accepted_at`` is NULL (no acceptance record)
     c. ``onboarding_completed=True`` AND no ``fiesta_profile`` row OR
        ``fiesta_profile.nic`` is NULL  (mismatched profile-completion state)

5. ``Joins User + Subscription + (Stripe customer ID column)`` -- left-outer
   join to ``fiesta_profile`` + ``paywall_subscription`` + count of active
   subscription rows. The Stripe customer id lives on ``user.stripe_customer_id``
   (added by ``add_admin_and_stripe_columns_to_user.py`` in this same commit).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from flask import (
    Blueprint,
    Flask,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy import or_, func, text

# Import the admin gate from the FIESTA auth package (Wave 6 canonical path).
from fiesta.auth.decorators import admin_required

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Blueprint
# --------------------------------------------------------------------------- #
fiesta_admin_bp = Blueprint(
    "fiesta_admin",
    __name__,
    template_folder="../../templates",
)


# Per-page cap. Matches the legacy admin_users page (per_page=10).
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100

# Stripe dashboard URL for the cached customer id. Live mode by default
# (matches stripe_config.py). Test mode users get the test-mode dashboard.
STRIPE_DASHBOARD_LIVE = "https://dashboard.stripe.com/customers/"
STRIPE_DASHBOARD_TEST = "https://dashboard.stripe.com/test/customers/"


# --------------------------------------------------------------------------- #
# Subscription-tier helpers (legacy column → human label)
# --------------------------------------------------------------------------- #
_TIER_LABEL = {
    "free_trial": "Trial",
    "trial": "Trial",
    "self_file": "Self-File",
    "auto_file": "Auto-File",
    None: "—",
    "": "—",
}

# Subscription *status* — same column today, but conceptually distinct. The
# subscription_status column conflates the two (it stores tier OR a payment
# state like 'past_due'/'cancelled'). We split for display.
_PAYMENT_STATES = {"active", "cancelled", "past_due", "trial", "free_trial"}


def _tier_label(subscription_status: Optional[str]) -> str:
    if subscription_status in _TIER_LABEL:
        return _TIER_LABEL[subscription_status]
    # Unknown value — show it verbatim so admin sees the truth.
    return subscription_status or "—"


def _status_label(subscription_status: Optional[str]) -> str:
    """Map the legacy column onto the 4-value status taxonomy."""
    if not subscription_status:
        return "—"
    s = subscription_status.lower()
    if s in ("self_file", "auto_file"):
        return "active"
    if s == "free_trial":
        return "trial"
    if s == "cancelled":
        return "cancelled"
    if s == "past_due":
        return "past_due"
    return s


# --------------------------------------------------------------------------- #
# Audit health
# --------------------------------------------------------------------------- #
def _compute_audit_health(user, fiesta_profile=None) -> Dict[str, Any]:
    """Return {"healthy": bool, "reasons": [str, ...]} for a single User row.

    Healthy iff ALL of:
      * ``tos_accepted_version`` is non-empty
      * ``tos_accepted_at`` is non-null
      * If ``onboarding_completed`` is True → ``fiesta_profile`` row exists
        AND ``fiesta_profile.nic`` is non-empty
    """
    reasons = []
    if not getattr(user, "tos_accepted_version", None):
        reasons.append("missing tos_accepted_version")
    if not getattr(user, "tos_accepted_at", None):
        reasons.append("no ToS acceptance record")
    if getattr(user, "onboarding_completed", False):
        if fiesta_profile is None:
            reasons.append("onboarded but no profile row")
        elif not getattr(fiesta_profile, "nic", None):
            reasons.append("onboarded but profile NIC missing")
    return {
        "healthy": not reasons,
        "reasons": reasons,
    }


# --------------------------------------------------------------------------- #
# Query builder
# --------------------------------------------------------------------------- #
def _build_user_query(*, search: str, tier: str, audit_status: str):
    """Return a SQLAlchemy query for the User list with filters applied.

    NOTE: we do NOT use a complex SELECT with raw joins because we want each
    row's ``fiesta_profile`` available for the audit-health computation. We
    join in Python via a per-page dict lookup — cheap (DEFAULT_PER_PAGE ≤
    100) and avoids ORM relationship configuration just for this view.
    """
    from models import User

    q = User.query

    if search:
        like = f"%{search}%"
        q = q.filter(or_(User.email.ilike(like), User.name.ilike(like)))

    if tier and tier != "all":
        # Match the legacy column. 'trial' encompasses both 'trial' and
        # 'free_trial'; the others are direct.
        if tier == "trial":
            q = q.filter(
                or_(User.subscription_status == "trial",
                    User.subscription_status == "free_trial")
            )
        else:
            q = q.filter(User.subscription_status == tier)

    # ``audit_status`` filter is applied AFTER pagination because the audit
    # computation needs the profile join (see paginated_users() below).
    # We do, however, narrow the SQL when 'unhealthy' is requested using
    # the cheap proxies (missing tos_accepted_version / tos_accepted_at).
    # Profile-mismatch users still fall through and are caught in Python.
    if audit_status == "unhealthy":
        q = q.filter(
            or_(
                User.tos_accepted_version.is_(None),
                User.tos_accepted_version == "",
                User.tos_accepted_at.is_(None),
                User.onboarding_completed.is_(True),  # profile join handles the rest
            )
        )
    elif audit_status == "healthy":
        q = q.filter(
            User.tos_accepted_version.isnot(None),
            User.tos_accepted_version != "",
            User.tos_accepted_at.isnot(None),
        )

    q = q.order_by(User.created_at.desc().nullslast(), User.id.desc())
    return q


def _enrich_rows(users) -> list[Dict[str, Any]]:
    """For each User on this page, attach: profile, audit_health, tier label,
    status label, stripe link. Returns a list of plain dicts the template
    consumes.
    """
    from models import User
    user_ids = [u.id for u in users]
    if not user_ids:
        return []

    # Load profiles in a single IN-query.
    profile_map = {}
    try:
        from fiesta.profile.models import FiestaProfile
        for p in FiestaProfile.query.filter(FiestaProfile.user_id.in_(user_ids)).all():
            profile_map[p.user_id] = p
    except Exception as e:
        log.warning("fiesta_profile lookup failed; audit-health falls back to NULL: %s", e)

    rows = []
    for u in users:
        prof = profile_map.get(u.id)
        health = _compute_audit_health(u, prof)
        stripe_id = getattr(u, "stripe_customer_id", None)
        stripe_url = None
        if stripe_id:
            # Stripe lets you open a 'cus_XXX' id in the live or test
            # dashboard; both work for either mode. We pick live as default
            # since that's the prod webhook target — admins viewing test
            # users can swap '/test/' into the URL.
            stripe_url = STRIPE_DASHBOARD_LIVE + stripe_id
        rows.append({
            "id": u.id,
            "email": u.email,
            "name": u.name or "",
            "persona": u.persona or "—",
            "tier_label": _tier_label(u.subscription_status),
            "status_label": _status_label(u.subscription_status),
            "created_at": u.created_at,
            # User has no last_login column today — see ambiguity note.
            "last_login": None,  # TODO(main-context): wire up session/login tracking
            "stripe_customer_id": stripe_id or "",
            "stripe_url": stripe_url,
            "audit_healthy": health["healthy"],
            "audit_reasons": health["reasons"],
        })
    return rows


def _post_filter_audit(rows, audit_status):
    """Apply the audit-status filter on enriched rows (catches the
    profile-mismatch case that SQL alone can't see)."""
    if audit_status == "unhealthy":
        return [r for r in rows if not r["audit_healthy"]]
    if audit_status == "healthy":
        return [r for r in rows if r["audit_healthy"]]
    return rows


# --------------------------------------------------------------------------- #
# View
# --------------------------------------------------------------------------- #
@fiesta_admin_bp.route("/admin/fie/users", methods=["GET"])
@admin_required
def fiesta_admin_users():
    """Paginated FIESTA users list."""
    search = (request.args.get("search") or "").strip()
    tier = (request.args.get("tier") or "all").strip().lower()
    audit_status = (request.args.get("audit") or "all").strip().lower()
    if audit_status not in ("all", "healthy", "unhealthy"):
        audit_status = "all"
    if tier not in ("all", "trial", "free_trial", "self_file", "auto_file",
                    "cancelled", "past_due"):
        tier = "all"

    page = max(1, request.args.get("page", default=1, type=int) or 1)
    per_page = request.args.get("per_page", default=DEFAULT_PER_PAGE, type=int) or DEFAULT_PER_PAGE
    per_page = min(MAX_PER_PAGE, max(1, per_page))

    try:
        q = _build_user_query(search=search, tier=tier, audit_status=audit_status)
        # We pull a buffer larger than per_page when audit_status filters in
        # Python so the page doesn't end up under-filled. Cheap because
        # DEFAULT_PER_PAGE * 4 ≤ 400.
        if audit_status == "all":
            pagination = q.paginate(page=page, per_page=per_page, error_out=False)
            users_on_page = pagination.items
            total = pagination.total
            total_pages = pagination.pages
        else:
            # Load wider, post-filter, then slice.
            buffer = per_page * 4
            wider = q.limit(buffer * page).all()
            enriched = _enrich_rows(wider)
            filtered = _post_filter_audit(enriched, audit_status)
            total = len(filtered)
            total_pages = max(1, (total + per_page - 1) // per_page)
            start = (page - 1) * per_page
            end = start + per_page
            sliced = filtered[start:end]
            return render_template(
                "fiesta_admin/users.html",
                rows=sliced,
                total=total,
                total_pages=total_pages,
                page=page,
                per_page=per_page,
                search=search,
                tier=tier,
                audit_status=audit_status,
            )

        rows = _enrich_rows(users_on_page)
        return render_template(
            "fiesta_admin/users.html",
            rows=rows,
            total=total,
            total_pages=total_pages,
            page=page,
            per_page=per_page,
            search=search,
            tier=tier,
            audit_status=audit_status,
        )
    except Exception as e:
        log.exception("S15 admin users page failed: %s", e)
        flash("Could not load the users list. The error was logged.", "error")
        # Render an empty table rather than 500 — admins can retry / use search.
        return render_template(
            "fiesta_admin/users.html",
            rows=[],
            total=0,
            total_pages=1,
            page=1,
            per_page=per_page,
            search=search,
            tier=tier,
            audit_status=audit_status,
            error_message=str(e),
        )


# --------------------------------------------------------------------------- #
# C9 F8.8 — /admin/fie/settings — DB-backed system settings
# --------------------------------------------------------------------------- #
#
# Separate from the legacy /admin/settings route in admin_routes.py (which
# still reads module-global dicts and is NOT modified here per W4-scope).
# This new handler reads/writes via SystemSetting (models.py C9) so edits
# survive pod restarts.
#
# URL is /admin/fie/settings to stay within the fiesta_admin blueprint's
# /admin/fie/* namespace and avoid Flask routing conflicts with the legacy
# app-level /admin/settings route.
# --------------------------------------------------------------------------- #

@fiesta_admin_bp.route("/admin/fie/settings", methods=["GET"])
@admin_required
def fie_settings_get():
    """GET /admin/fie/settings — render all SystemSetting rows as a form."""
    from models import SystemSetting

    settings = SystemSetting.all_settings()
    return render_template(
        "admin/fie_settings.html",
        settings=settings,
    )


@fiesta_admin_bp.route("/admin/fie/settings", methods=["POST"])
@admin_required
def fie_settings_post():
    """POST /admin/fie/settings — update one or more SystemSetting rows."""
    from models import SystemSetting
    from flask_login import current_user

    form = request.form.to_dict()
    # Strip Flask-WTF CSRF token from the form dict (not a setting key).
    form.pop("csrf_token", None)

    updated = []
    errors = []
    for key, raw_value in form.items():
        try:
            # Attempt JSON decode so numbers/booleans round-trip correctly.
            import json as _json
            try:
                value = _json.loads(raw_value)
            except (ValueError, TypeError):
                # Treat as plain string if JSON decode fails.
                value = raw_value
            SystemSetting.set(
                key,
                value,
                user_id=getattr(current_user, "id", None),
            )
            updated.append(key)
        except Exception as exc:
            errors.append(f"{key}: {exc}")
            log.exception("SystemSetting.set failed for key=%s: %s", key, exc)

    if errors:
        flash(f"Some settings failed to save: {'; '.join(errors)}", "danger")
    else:
        flash(f"Settings saved ({len(updated)} keys updated).", "success")

    return redirect(url_for("fiesta_admin.fie_settings_get"))


# --------------------------------------------------------------------------- #
# Public registration hook
# --------------------------------------------------------------------------- #
def register_routes(app: Flask) -> None:
    """Standard FIESTA blueprint hook called from main.py."""
    if "fiesta_admin" in app.blueprints:
        log.debug("fiesta_admin blueprint already registered — skipping.")
        return
    app.register_blueprint(fiesta_admin_bp)
    log.info("S15 fiesta_admin blueprint registered (/admin/fie/users, /admin/fie/settings)")


# --------------------------------------------------------------------------- #
# C10 F8.9 — Per-tax-year submission admin view
# --------------------------------------------------------------------------- #
@fiesta_admin_bp.route("/admin/submissions", methods=["GET"])
@admin_required
def submissions_view():
    """C10 F8.9 — Per-tax-year submission filter for operator visibility.

    Query params:
      tax_year  — any accepted form (25/26, 2025/26, 25_26).  Normalised to
                  canonical "YYYY/YYYY" before filtering.
      status    — raw status string (preparing, attested, export-generated, …).

    Acceptance: an operator can answer "how many users completed filing for
    25/26?" from this one page.
    """
    from fiesta.submit.models import Submission
    from models import User

    tax_year_raw = (request.args.get("tax_year") or "").strip()
    status_raw = (request.args.get("status") or "").strip()

    # ------------------------------------------------------------------ #
    # Build base query, apply filters
    # ------------------------------------------------------------------ #
    q = Submission.query

    tax_year_normalised = tax_year_raw  # fallback: pass through as-is
    if tax_year_raw:
        try:
            # D4 fix (Stage C4 admin consolidation): Submission.tax_year is
            # persisted in S5 form ("YYYY/YYYY", e.g. "2025/2026") — see
            # fiesta/submit/models.py docstring. The S4 normaliser returns
            # "YYYY-YY" which never matches the column and crashes if the
            # underlying enum coercion fails. Use the S5 normaliser so that
            # any accepted input form (25/26, 2025/26, 2025/2026, 25_26, ...)
            # round-trips to the canonical S5 string the column actually holds.
            from fiesta.tax_bill.aggregator import normalise_tax_year_to_s5_format
            tax_year_normalised = normalise_tax_year_to_s5_format(tax_year_raw)
            q = q.filter(Submission.tax_year == tax_year_normalised)
        except Exception:
            # If normaliser fails (bad input), filter on the raw string so the
            # operator sees an empty result rather than an unfiltered dump.
            q = q.filter(Submission.tax_year == tax_year_raw)

    if status_raw:
        q = q.filter(Submission.status == status_raw)

    submissions = q.order_by(Submission.created_at.desc()).limit(500).all()

    # ------------------------------------------------------------------ #
    # Aggregate counts by status for the header summary strip
    # ------------------------------------------------------------------ #
    status_counts: dict[str, int] = {}
    for s in submissions:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1

    # ------------------------------------------------------------------ #
    # Distinct tax years for filter dropdown (all rows, not just current page)
    # ------------------------------------------------------------------ #
    distinct_years_rows = (
        Submission.query
        .with_entities(Submission.tax_year)
        .distinct()
        .order_by(Submission.tax_year.desc())
        .all()
    )
    distinct_years = [r.tax_year for r in distinct_years_rows if r.tax_year]

    # ------------------------------------------------------------------ #
    # Possible statuses for filter dropdown
    # ------------------------------------------------------------------ #
    distinct_statuses_rows = (
        Submission.query
        .with_entities(Submission.status)
        .distinct()
        .order_by(Submission.status)
        .all()
    )
    possible_statuses = sorted({r.status for r in distinct_statuses_rows if r.status})

    # ------------------------------------------------------------------ #
    # Enrich rows with user email (single IN-query — avoids N+1)
    # ------------------------------------------------------------------ #
    user_ids = list({s.user_id for s in submissions})
    user_email_map: dict[int, str] = {}
    if user_ids:
        try:
            users = User.query.filter(User.id.in_(user_ids)).with_entities(
                User.id, User.email
            ).all()
            user_email_map = {u.id: u.email for u in users}
        except Exception as exc:
            log.warning("submissions_view: user email lookup failed: %s", exc)

    enriched = [
        {
            "id": s.id,
            "user_id": s.user_id,
            "user_email": user_email_map.get(s.user_id, "—"),
            "tax_year": s.tax_year,
            "status": s.status,
            "final_tax_payable_lkr": s.final_tax_payable_lkr,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s in submissions
    ]

    return render_template(
        "admin/submissions.html",
        submissions=enriched,
        tax_year=tax_year_raw,
        tax_year_normalised=tax_year_normalised,
        status=status_raw,
        status_counts=status_counts,
        distinct_years=distinct_years,
        possible_statuses=possible_statuses,
        capped=(len(submissions) == 500),
    )
