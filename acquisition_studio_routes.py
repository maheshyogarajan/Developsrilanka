"""
Acquisition Studio admin routes — Subagent D (2026-05-18).

Three endpoints under `/ai_org/admin/acquisition_studio` — match Subagent C's
url_prefix gotcha (Blueprint has url_prefix="/ai_org" so admin routes become
`/ai_org/admin/acquisition_studio`, NOT `/admin/ai_org/...`).

  GET  /ai_org/admin/acquisition_studio           — HTML dashboard
  GET  /ai_org/admin/acquisition_studio.json      — JSON variant
  POST /ai_org/admin/acquisition_studio/run_pass  — manual orchestrator trigger

login_required + role=='admin' gate, same pattern as ai_org_score_routes /
customer_brain_routes.

Wiring: orchestrator calls register_routes(app) at app init.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    abort,
    flash,
)
from flask_login import login_required, current_user

log = logging.getLogger(__name__)

acquisition_studio_bp = Blueprint(
    "acquisition_studio",
    __name__,
    url_prefix="/ai_org",
)


def _require_admin():
    """Raise 403 if current_user is not admin. login_required upstream handles
    the unauthenticated case."""
    role = getattr(current_user, "role", None)
    if role != "admin":
        abort(403)


# --------------------------------------------------------------------------- #
# Data assembly — used by both HTML + JSON variants.
# --------------------------------------------------------------------------- #

def _dashboard_data():
    """Build the dashboard data structure used by HTML + JSON variants.

    Returns:
      {
        "counts_24h": {proposals, contracts, deliverables, red_team_rejections,
                       cac_rejections},
        "counts_7d":  same shape,
        "recent_proposals": [ {id, opportunity_slug, status, cac_forecast,
                               quoted_price_lkr, submitted_at, ...} x20 ],
        "studio": {slug, id, status_score, status_band, last_computed_at},
        "as_of": isoformat string,
      }
    """
    from ai_org_models import (
        AIOrg, Proposal, Contract, Deliverable, ReputationEvent,
    )

    out = {
        "counts_24h": {
            "proposals": 0, "contracts": 0, "deliverables": 0,
            "red_team_rejections": 0, "cac_rejections": 0,
        },
        "counts_7d": {
            "proposals": 0, "contracts": 0, "deliverables": 0,
            "red_team_rejections": 0, "cac_rejections": 0,
        },
        "recent_proposals": [],
        "studio": None,
        "as_of": datetime.utcnow().isoformat(),
    }

    try:
        from acquisition_studio_org import STUDIO_SLUG
        studio = AIOrg.query.filter_by(slug=STUDIO_SLUG).first()
    except Exception as e:
        log.warning(f"_dashboard_data: studio lookup failed: {e}")
        studio = None

    if studio is None:
        return out

    out["studio"] = {
        "id": studio.id,
        "slug": studio.slug,
        "name": studio.name,
        "status_score": float(studio.status_score) if studio.status_score is not None else None,
        "status_band": studio.status_band,
        "last_computed_at": (
            studio.last_score_computed_at.isoformat()
            if studio.last_score_computed_at else None
        ),
    }

    now = datetime.utcnow()
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)

    try:
        out["counts_24h"]["proposals"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Proposal.submitted_at >= h24)
            .count()
        )
        out["counts_7d"]["proposals"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Proposal.submitted_at >= d7)
            .count()
        )
        out["counts_24h"]["contracts"] = (
            Contract.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Contract.started_at >= h24)
            .count()
        )
        out["counts_7d"]["contracts"] = (
            Contract.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Contract.started_at >= d7)
            .count()
        )
        out["counts_24h"]["deliverables"] = (
            Deliverable.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Deliverable.delivered_at >= h24)
            .count()
        )
        out["counts_7d"]["deliverables"] = (
            Deliverable.query
            .filter_by(proposer_org_id=studio.id)
            .filter(Deliverable.delivered_at >= d7)
            .count()
        )
        from acquisition_studio_org import STATUS_REJECT_RED, STATUS_REJECT_CAC
        out["counts_24h"]["red_team_rejections"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id, status=STATUS_REJECT_RED)
            .filter(Proposal.decided_at >= h24)
            .count()
        )
        out["counts_7d"]["red_team_rejections"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id, status=STATUS_REJECT_RED)
            .filter(Proposal.decided_at >= d7)
            .count()
        )
        out["counts_24h"]["cac_rejections"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id, status=STATUS_REJECT_CAC)
            .filter(Proposal.decided_at >= h24)
            .count()
        )
        out["counts_7d"]["cac_rejections"] = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id, status=STATUS_REJECT_CAC)
            .filter(Proposal.decided_at >= d7)
            .count()
        )
    except Exception as e:
        log.warning(f"_dashboard_data: counts query failed: {e}")

    try:
        recent = (
            Proposal.query
            .filter_by(proposer_org_id=studio.id)
            .order_by(Proposal.submitted_at.desc())
            .limit(20)
            .all()
        )
        for p in recent:
            payload = p.artifact_payload or {}
            out["recent_proposals"].append({
                "id": p.id,
                "opportunity_slug": p.opportunity_slug,
                "artifact_kind": p.artifact_kind,
                "status": p.status,
                "quoted_price_lkr": (
                    float(p.quoted_price_lkr) if p.quoted_price_lkr is not None else None
                ),
                "cac_forecast": payload.get("cac_forecast"),
                "cac_analyst_decision": payload.get("cac_analyst_decision"),
                "red_team_decision": payload.get("red_team_decision"),
                "red_team_reason": payload.get("red_team_reason"),
                "trigger_kind": payload.get("trigger_kind"),
                "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
                "decided_at": p.decided_at.isoformat() if p.decided_at else None,
            })
    except Exception as e:
        log.warning(f"_dashboard_data: recent_proposals query failed: {e}")

    return out


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@acquisition_studio_bp.route(
    "/admin/acquisition_studio",
    methods=["GET"],
    endpoint="admin_dashboard",
)
@login_required
def admin_dashboard():
    """Admin-only HTML dashboard."""
    _require_admin()
    data = _dashboard_data()
    return render_template("admin/acquisition_studio.html", data=data)


@acquisition_studio_bp.route(
    "/admin/acquisition_studio.json",
    methods=["GET"],
    endpoint="admin_dashboard_json",
)
@login_required
def admin_dashboard_json():
    """Admin-only JSON variant."""
    _require_admin()
    return jsonify(_dashboard_data())


@acquisition_studio_bp.route(
    "/admin/acquisition_studio/run_pass",
    methods=["POST"],
    endpoint="admin_run_pass",
)
@login_required
def admin_run_pass():
    """Admin-only: trigger run_pass() out of band. Synchronous (fast — single
    scan + per-trigger lifecycle). Returns the run_pass summary as a flash +
    redirect for the HTML view, or JSON if Accept: application/json.
    """
    _require_admin()
    try:
        from acquisition_studio_org import run_pass
        summary = run_pass()
    except Exception as e:
        log.warning(f"admin_run_pass failed: {e}")
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": str(e)}), 500
        flash(f"run_pass failed: {e}", "danger")
        return redirect(url_for("acquisition_studio.admin_dashboard"))

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True, "summary": summary})

    flash(
        f"run_pass done: {summary.get('proposals_submitted', 0)} proposals, "
        f"{summary.get('contracts_signed', 0)} contracts, "
        f"{summary.get('deliverables_completed', 0)} deliverables, "
        f"{summary.get('red_team_rejections', 0)} red-team rejections, "
        f"{summary.get('cac_rejections', 0)} CAC rejections.",
        "success",
    )
    return redirect(url_for("acquisition_studio.admin_dashboard"))


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def register_routes(app) -> None:
    """Register the Acquisition Studio blueprint. Idempotent — skip if already
    registered (mirrors ai_org_score_routes pattern).
    """
    if "acquisition_studio" in app.blueprints:
        log.info("acquisition_studio blueprint already registered; skipping.")
        return
    app.register_blueprint(acquisition_studio_bp)
    log.info(
        "acquisition_studio blueprint registered: "
        "/ai_org/admin/acquisition_studio[.json], "
        "/ai_org/admin/acquisition_studio/run_pass"
    )


__all__ = ["acquisition_studio_bp", "register_routes"]
