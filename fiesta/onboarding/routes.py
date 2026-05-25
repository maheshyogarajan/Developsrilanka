"""G4 Unified Onboarding — backend routes (MS4 W3e, 2026-05-25).

Replaces TWO existing onboarding surfaces with ONE flow:

  - `/onboarding`  (legacy business-org wizard) → escape hatch only at
    `/onboarding/legacy`. Primary URL routes to `/onboarding/welcome`
    for users who still need onboarding.
  - `/fie/triage`  (FIESTA 3-question triage) → kept for users who want
    to update their tax profile via a dedicated "Tax profile" tab on
    `/fie/profile`; sl_foreign_income personas with empty
    `income_sources` are routed to `/onboarding/welcome` instead.

The new flow is three GETs + one POST + one JSON endpoint:

  - GET  /onboarding/welcome         — value-prop + "Get started" CTA
  - GET  /onboarding/income-sources  — full-page render of the
                                       reusable G3.6 picker partial
                                       (templates/_fiesta/income_source_picker.html)
  - GET  /onboarding/confirm         — confirmation card: enabled modules
                                       + recommended next step
  - POST /onboarding/confirm         — mark User.onboarding_completed=True,
                                       redirect to recommended_next or '/'
  - GET  /api/fiesta/onboarding-state — JSON of current step / income_sources
                                        / can_skip / recommended_next

Design contract
---------------
- Reuses the G3.6 picker partial. Does NOT duplicate it. The picker's
  own JS still owns the AJAX submit to /api/fiesta/income-sources.
  Onboarding only renders the partial inside a step-2 page.
- After the picker write completes (via G3.6 endpoint), the user clicks
  "Continue" to navigate to /onboarding/confirm. We rely on the picker's
  built-in save semantics; no double-write here.
- The "Continue" CTA on the picker page submits a separate form to
  /onboarding/confirm so JS-disabled users still get there. The G3.6
  picker form auto-submits the income_sources to its own endpoint
  before the user clicks Continue.
- `User.onboarding_completed` becomes True on POST to /onboarding/confirm,
  matching the existing semantics. Email verification is still required
  before onboarding (existing pattern).
- The recommended next step matches the W2 Agent 1 funnel-state ranking
  (business > rsu > crypto > employment > rental > investment > other,
  with foreign_remittance handled as its own track).

Contract verified by tests/platform/test_g4_unified_onboarding.py.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import (
    Blueprint,
    Flask,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required


log = logging.getLogger(__name__)


bp = Blueprint(
    "fiesta_onboarding",
    __name__,
    url_prefix="/onboarding",
    template_folder="../../templates",
)


# ---------------------------------------------------------------------------
# Recommended-next-step computation — mirrors the W2 Agent 1 funnel-state
# precedence (`app._compute_non_remittance_next_step`) so the confirm step
# routes the user to the same place the hub would have surfaced.
# ---------------------------------------------------------------------------


def _recommended_next(user: Any) -> Dict[str, str]:
    """Return {"label": str, "href": str, "rationale": str} for the user's
    highest-priority next step after onboarding.

    Precedence (matches the hub funnel-state recommender so we don't
    contradict it):
        foreign_remittance → /remittance
        business_*         → /earnings (quarterly receipts)
        rsu                → /income/rsu/import
        crypto             → /income/crypto/disposals
        employment_lkr     → /earnings (payslip)
        rental_*           → /earnings (rent)
        investment_*       → /earnings (interest/dividend/CGT)
        professional_fees  → /earnings
        other              → /fie/profile
        empty              → /fie/income-sources (loop back — user
                              probably skipped without picking anything)
    """
    sources = set(getattr(user, "income_sources", None) or [])

    if not sources:
        return {
            "label": "Pick your income types",
            "href": "/fie/income-sources",
            "rationale": (
                "You skipped the income-types step. Tick at least one so we "
                "can surface the right modules and the right calculator."
            ),
        }

    if "foreign_remittance" in sources:
        return {
            "label": "Log your first remittance",
            "href": "/remittance",
            "rationale": (
                "Foreign-income earners report on remittance basis. Log the "
                "USD/EUR/GBP that hit your SL bank this period."
            ),
        }

    if "business_lkr" in sources or "business_foreign" in sources:
        return {
            "label": "Log this quarter's business receipts",
            "href": "/earnings",
            "rationale": (
                "Quarterly receipt totals are the spine of your business "
                "income schedule."
            ),
        }

    if "rsu" in sources:
        return {
            "label": "Import your RSU vesting events",
            "href": "/income/rsu/import",
            "rationale": (
                "Each vesting tranche is taxable at FMV under IRA §5(2)(j). "
                "Log the latest to refresh your YTD bill."
            ),
        }

    if "crypto" in sources:
        return {
            "label": "Log your crypto disposals",
            "href": "/income/crypto/disposals",
            "rationale": (
                "Every disposal (sale, swap, on-chain transfer that "
                "realises) needs FMV at the disposal date."
            ),
        }

    if "employment_lkr" in sources:
        return {
            "label": "Log your most recent payslip",
            "href": "/earnings",
            "rationale": (
                "Your latest payslip drives the PAYE reconciliation and "
                "the documented-tax-credit baseline."
            ),
        }

    if "rental_lkr" in sources or "rental_foreign" in sources:
        return {
            "label": "Log this month's rental income",
            "href": "/earnings",
            "rationale": (
                "Net rental (rent minus deductible expenses) is the IRD "
                "basis."
            ),
        }

    if "investment_lkr" in sources or "investment_foreign" in sources:
        return {
            "label": "Log this period's investment income",
            "href": "/earnings",
            "rationale": (
                "Interest, dividend and CGT events all need date-and-amount "
                "logging."
            ),
        }

    if "professional_fees_lkr" in sources:
        return {
            "label": "Log your most recent fee invoice",
            "href": "/earnings",
            "rationale": (
                "Professional fees often have §85 WHT deducted at source — "
                "log them to claim the credit back."
            ),
        }

    # 'other' / fallback
    return {
        "label": "Complete your profile",
        "href": "/fie/profile",
        "rationale": (
            "Finish the basic profile (NIC, address, residency) so we can "
            "personalise everything going forward."
        ),
    }


def _current_step(user: Any) -> str:
    """Infer which step the user is on:

      'welcome'        — onboarding_completed=False AND income_sources=[]
      'income-sources' — onboarding_completed=False AND income_sources!=[]
                          (saved but not yet confirmed)
      'confirm'        — same as 'income-sources' (the confirm screen is
                          the natural next page after picking)
      'complete'       — onboarding_completed=True
    """
    if getattr(user, "onboarding_completed", False):
        return "complete"
    sources = list(getattr(user, "income_sources", None) or [])
    if not sources:
        return "welcome"
    return "confirm"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bp.route("/welcome", methods=["GET"], strict_slashes=False)
@login_required
def welcome():
    """Step 1 — value-prop + Get-started CTA.

    Email verification still required first (existing pattern). If the
    user has already completed onboarding, bounce to '/' so they don't
    see the welcome screen twice — UNLESS they hit ?restart=1 from the
    profile-page "Restart onboarding" link, in which case we flip the
    onboarding_completed flag back to False and let them re-run the
    flow. The restart does NOT touch income_sources (the picker is
    additive); the user's existing selections are pre-ticked when they
    revisit step 2.
    """
    if not getattr(current_user, "is_email_verified", False):
        flash("Please verify your email before completing setup.", "warning")
        return redirect(url_for("verify_email_reminder"))

    # G4 restart path — only when explicitly requested.
    if (
        request.args.get("restart") == "1"
        and getattr(current_user, "onboarding_completed", False)
    ):
        try:
            from app import db
            current_user.onboarding_completed = False
            db.session.add(current_user)
            db.session.commit()
            log.info(
                "G4 onboarding: user %s restarted onboarding via "
                "?restart=1 (income_sources preserved=%s)",
                current_user.id,
                list(getattr(current_user, "income_sources", None) or []),
            )
        except Exception as exc:
            log.exception("G4 restart: failed to flip flag: %s", exc)
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
            flash(
                "Couldn't restart onboarding right now. Please try again.",
                "danger",
            )
            return redirect("/fie/profile")

    if getattr(current_user, "onboarding_completed", False):
        return redirect("/")

    return render_template("fiesta_onboarding/welcome.html")


@bp.route("/income-sources", methods=["GET"], strict_slashes=False)
@login_required
def income_sources_step():
    """Step 2 — full-page render of the reusable G3.6 picker partial.

    The picker JS owns the AJAX POST to /api/fiesta/income-sources.
    Once saved, the user clicks Continue (a form POST to /onboarding/confirm).
    """
    if not getattr(current_user, "is_email_verified", False):
        flash("Please verify your email before completing setup.", "warning")
        return redirect(url_for("verify_email_reminder"))

    if getattr(current_user, "onboarding_completed", False):
        return redirect("/")

    return render_template("fiesta_onboarding/income_sources.html")


@bp.route("/confirm", methods=["GET"], strict_slashes=False)
@login_required
def confirm_step():
    """Step 3 — confirmation card showing enabled modules + recommended next."""
    if not getattr(current_user, "is_email_verified", False):
        flash("Please verify your email before completing setup.", "warning")
        return redirect(url_for("verify_email_reminder"))

    if getattr(current_user, "onboarding_completed", False):
        return redirect("/")

    sources = list(getattr(current_user, "income_sources", None) or [])
    rec = _recommended_next(current_user)
    return render_template(
        "fiesta_onboarding/confirm.html",
        income_sources=sources,
        recommended=rec,
    )


@bp.route("/confirm", methods=["POST"], strict_slashes=False)
@login_required
def confirm_submit():
    """Mark User.onboarding_completed=True; redirect to recommended next."""
    if not getattr(current_user, "is_email_verified", False):
        flash("Please verify your email before completing setup.", "warning")
        return redirect(url_for("verify_email_reminder"))

    # Already-complete check — re-running POST is idempotent; just redirect.
    if not getattr(current_user, "onboarding_completed", False):
        try:
            from app import db
            current_user.onboarding_completed = True
            db.session.add(current_user)
            db.session.commit()
            log.info(
                "G4 onboarding: user %s marked onboarding_completed=True "
                "via unified flow (income_sources=%s)",
                current_user.id,
                list(getattr(current_user, "income_sources", None) or []),
            )

            # Event spine emit for the funnel dashboard.
            try:
                from events import emit as _emit_spine
                _emit_spine(
                    "onboarding_completed",
                    user_id=current_user.id,
                    payload={
                        "flow": "g4_unified",
                        "income_sources": list(
                            getattr(current_user, "income_sources", None) or []
                        ),
                    },
                    source="route:fiesta_onboarding.confirm_submit",
                )
            except Exception as _exc:
                log.debug("g4 onboarding_completed spine emit failed: %s", _exc)
        except Exception as exc:
            log.exception("G4 confirm_submit: failed to persist: %s", exc)
            try:
                from app import db
                db.session.rollback()
            except Exception:
                pass
            flash(
                "We hit a snag finishing setup. Please try again.", "danger"
            )
            return redirect(url_for("fiesta_onboarding.confirm_step"))

    rec = _recommended_next(current_user)
    return redirect(rec.get("href") or "/")


@bp.route("/legacy", methods=["GET", "POST"], strict_slashes=False)
@login_required
def legacy_wizard():
    """Escape hatch — preserves access to the original business-org wizard
    for one sprint. Renders the same template the legacy `/onboarding`
    route used (templates/onboarding_wizard.html). The legacy POST handler
    lives at /onboarding (which now does the legacy work for the
    `?force=legacy=1` path); this GET-only escape hatch keeps the surface
    discoverable without rewiring the legacy form.

    The legacy POST flow is preserved by the parent `/onboarding` route
    (see app.py) when the request comes with `?legacy=1` or the user
    explicitly posts a `business_name`. This route just renders the form
    pointed at /onboarding so the existing POST handler keeps working.
    """
    # GET only — legacy POST is still handled by app.onboarding_wizard.
    if request.method == "POST":
        # Forward to the legacy handler so the existing business-org
        # creation logic runs verbatim.
        from urllib.parse import urlencode
        qs = urlencode({"legacy": "1"})
        return redirect(f"/onboarding?{qs}", code=307)

    return render_template(
        "onboarding_wizard.html",
        is_legacy_escape_hatch=True,
    )


# ---------------------------------------------------------------------------
# JSON endpoint — client-side flow control
# ---------------------------------------------------------------------------


@bp.route("/state", methods=["GET"], strict_slashes=False)
@login_required
def _alias_state():
    """Alias /onboarding/state → /api/fiesta/onboarding-state for symmetry."""
    return _api_state_inner()


def _api_state_inner():
    sources = list(getattr(current_user, "income_sources", None) or [])
    step = _current_step(current_user)
    rec = _recommended_next(current_user)
    return jsonify({
        "ok": True,
        "step": step,
        "income_sources": sources,
        "can_skip": True,
        "recommended_next": rec.get("href") or "/",
        "recommended_label": rec.get("label") or "",
        "recommended_rationale": rec.get("rationale") or "",
        "onboarding_completed": bool(
            getattr(current_user, "onboarding_completed", False)
        ),
    })


# Standalone API endpoint (mounted directly on `app` in register_blueprint
# so the URL does not get the /onboarding prefix). See register_blueprint
# below for the wiring.
def _api_state_view():
    """View function bound to /api/fiesta/onboarding-state at app level."""
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "error": "auth required"}), 401
    return _api_state_inner()


# ---------------------------------------------------------------------------
# Blueprint registration
# ---------------------------------------------------------------------------


def register_blueprint(app: Flask) -> None:
    """Wire the G4 unified-onboarding routes into the Flask app. Idempotent."""
    if "fiesta_onboarding" in app.blueprints:
        log.debug(
            "fiesta_onboarding blueprint already registered, skipping"
        )
        return

    app.register_blueprint(bp)

    # Standalone JSON endpoint at /api/fiesta/onboarding-state.
    # Use add_url_rule directly so we don't need a second blueprint.
    if "fiesta_onboarding_api_state" not in app.view_functions:
        app.add_url_rule(
            "/api/fiesta/onboarding-state",
            endpoint="fiesta_onboarding_api_state",
            view_func=_api_state_view,
            methods=["GET"],
        )

    log.info(
        "G4 unified-onboarding blueprint registered "
        "(/onboarding/welcome, /onboarding/income-sources, "
        "/onboarding/confirm, /onboarding/legacy, "
        "/api/fiesta/onboarding-state)"
    )


__all__ = [
    "bp",
    "register_blueprint",
    "_recommended_next",
    "_current_step",
]
