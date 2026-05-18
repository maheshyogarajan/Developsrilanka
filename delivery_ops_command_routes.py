"""
Delivery Ops Command admin routes — Subagent E (2026-05-18).

Three endpoints under `/ai_org/admin/delivery_ops_command` — mirror Subagent D's
url_prefix pattern (Blueprint has url_prefix="/ai_org" so admin paths become
`/ai_org/admin/delivery_ops_command`, NOT `/admin/ai_org/...`).

  GET  /ai_org/admin/delivery_ops_command            — HTML dashboard
  GET  /ai_org/admin/delivery_ops_command.json       — JSON variant
  POST /ai_org/admin/delivery_ops_command/run_pass   — manual orchestrator trigger

login_required + role=='admin' gate.

Dashboard headline metric: First-Pass Completion Rate (FPCR) =
   delivered / (delivered + sla_breach + failed_qc)
… plus mean cycle-time over the last 24h + last 7d windows.

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

delivery_ops_bp = Blueprint(
    "delivery_ops_command",
    __name__,
    url_prefix="/ai_org",
)


def _require_admin():
    """Raise 403 if current_user is not admin."""
    role = getattr(current_user, "role", None)
    if role != "admin":
        abort(403)


# --------------------------------------------------------------------------- #
# Data assembly — used by both HTML + JSON variants.
# --------------------------------------------------------------------------- #

def _counts_for_window(Proposal, org_id: int, since: datetime, status_const: str) -> int:
    """Count Proposals for org in a given status decided in the window. For
    queued (no decided_at), submitted_at is the right anchor."""
    from delivery_ops_command_org import STATUS_QUEUED, STATUS_IN_FLIGHT
    q = Proposal.query.filter_by(proposer_org_id=org_id, status=status_const)
    if status_const in (STATUS_QUEUED, STATUS_IN_FLIGHT):
        q = q.filter(Proposal.submitted_at >= since)
    else:
        q = q.filter(Proposal.decided_at >= since)
    return q.count()


def _mean_cycle_time(Proposal, org_id: int, since: datetime) -> float:
    """Mean cycle_time_h across delivered + sla_breach proposals in the window.
    Returns 0.0 if no rows."""
    from delivery_ops_command_org import STATUS_DELIVERED, STATUS_SLA_BREACH
    rows = (
        Proposal.query
        .filter_by(proposer_org_id=org_id)
        .filter(Proposal.status.in_((STATUS_DELIVERED, STATUS_SLA_BREACH)))
        .filter(Proposal.decided_at >= since)
        .all()
    )
    samples = []
    for p in rows:
        payload = p.artifact_payload or {}
        ct = payload.get("cycle_time_h")
        if ct is not None:
            try:
                samples.append(float(ct))
            except (TypeError, ValueError):
                pass
    if not samples:
        return 0.0
    return sum(samples) / len(samples)


def _dashboard_data():
    """Build the dashboard data structure used by HTML + JSON variants.

    Returns dict with:
      counts_24h, counts_7d, fpcr_24h, fpcr_7d, mean_cycle_time_24h,
      mean_cycle_time_7d, recent_proposals (x20), org metadata, as_of.

    FPCR = delivered / (delivered + sla_breach + failed_qc). REJECTED_CAP is
    NOT in the denominator — it's an operational signal, not a completion
    attempt.
    """
    from ai_org_models import AIOrg, Proposal
    from delivery_ops_command_org import (
        ORG_SLUG,
        STATUS_QUEUED,
        STATUS_IN_FLIGHT,
        STATUS_DELIVERED,
        STATUS_SLA_BREACH,
        STATUS_FAILED_QC,
        STATUS_REJECTED_CAP,
        QUEUE_CAPACITY,
    )

    out = {
        "counts_24h": {
            "queued": 0, "in_flight": 0, "delivered": 0, "sla_breach": 0,
            "failed_qc": 0, "rejected_cap": 0,
        },
        "counts_7d": {
            "queued": 0, "in_flight": 0, "delivered": 0, "sla_breach": 0,
            "failed_qc": 0, "rejected_cap": 0,
        },
        "fpcr_24h": None,
        "fpcr_7d": None,
        "mean_cycle_time_24h": 0.0,
        "mean_cycle_time_7d": 0.0,
        "active_count": 0,
        "queue_capacity": QUEUE_CAPACITY,
        "recent_proposals": [],
        "org": None,
        "as_of": datetime.utcnow().isoformat(),
    }

    try:
        org = AIOrg.query.filter_by(slug=ORG_SLUG).first()
    except Exception as e:
        log.warning(f"_dashboard_data: org lookup failed: {e}")
        org = None

    if org is None:
        return out

    out["org"] = {
        "id": org.id,
        "slug": org.slug,
        "name": org.name,
        "status_score": float(org.status_score) if org.status_score is not None else None,
        "status_band": org.status_band,
        "last_computed_at": (
            org.last_score_computed_at.isoformat()
            if org.last_score_computed_at else None
        ),
    }

    now = datetime.utcnow()
    h24 = now - timedelta(hours=24)
    d7 = now - timedelta(days=7)

    try:
        # Active queue snapshot (queued+in_flight, all time — current state).
        out["active_count"] = (
            Proposal.query
            .filter_by(proposer_org_id=org.id)
            .filter(Proposal.status.in_((STATUS_QUEUED, STATUS_IN_FLIGHT)))
            .count()
        )
    except Exception as e:
        log.warning(f"_dashboard_data: active_count query failed: {e}")

    status_list = [
        ("queued", STATUS_QUEUED),
        ("in_flight", STATUS_IN_FLIGHT),
        ("delivered", STATUS_DELIVERED),
        ("sla_breach", STATUS_SLA_BREACH),
        ("failed_qc", STATUS_FAILED_QC),
        ("rejected_cap", STATUS_REJECTED_CAP),
    ]

    try:
        for label, const in status_list:
            out["counts_24h"][label] = _counts_for_window(Proposal, org.id, h24, const)
            out["counts_7d"][label] = _counts_for_window(Proposal, org.id, d7, const)
    except Exception as e:
        log.warning(f"_dashboard_data: window counts query failed: {e}")

    # FPCR — only meaningful when there's at least one completion attempt
    # (delivered + sla_breach + failed_qc) in the window.
    for window_key in ("24h", "7d"):
        c = out[f"counts_{window_key}"]
        denom = c["delivered"] + c["sla_breach"] + c["failed_qc"]
        out[f"fpcr_{window_key}"] = (
            (c["delivered"] / denom) if denom > 0 else None
        )

    try:
        out["mean_cycle_time_24h"] = _mean_cycle_time(Proposal, org.id, h24)
        out["mean_cycle_time_7d"] = _mean_cycle_time(Proposal, org.id, d7)
    except Exception as e:
        log.warning(f"_dashboard_data: mean_cycle_time failed: {e}")

    try:
        recent = (
            Proposal.query
            .filter_by(proposer_org_id=org.id)
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
                "job_kind": payload.get("job_kind"),
                "sla_target_h": payload.get("sla_target_h"),
                "cycle_time_h": payload.get("cycle_time_h"),
                "sla_outcome": payload.get("sla_outcome"),
                "quality_review_decision": payload.get("quality_review_decision"),
                "red_team_decision": payload.get("red_team_decision"),
                "rejection_reason": payload.get("rejection_reason"),
                "quoted_price_lkr": (
                    float(p.quoted_price_lkr) if p.quoted_price_lkr is not None else None
                ),
                "submitted_at": p.submitted_at.isoformat() if p.submitted_at else None,
                "decided_at": p.decided_at.isoformat() if p.decided_at else None,
            })
    except Exception as e:
        log.warning(f"_dashboard_data: recent_proposals query failed: {e}")

    return out


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@delivery_ops_bp.route(
    "/admin/delivery_ops_command",
    methods=["GET"],
    endpoint="admin_dashboard",
)
@login_required
def admin_dashboard():
    """Admin-only HTML dashboard."""
    _require_admin()
    data = _dashboard_data()
    return render_template("admin/delivery_ops_command.html", data=data)


@delivery_ops_bp.route(
    "/admin/delivery_ops_command.json",
    methods=["GET"],
    endpoint="admin_dashboard_json",
)
@login_required
def admin_dashboard_json():
    """Admin-only JSON variant."""
    _require_admin()
    return jsonify(_dashboard_data())


@delivery_ops_bp.route(
    "/admin/delivery_ops_command/run_pass",
    methods=["POST"],
    endpoint="admin_run_pass",
)
@login_required
def admin_run_pass():
    """Admin-only: trigger run_pass() out of band. Optional since_minutes query
    param. Synchronous (fast — single scan + per-event lifecycle).
    """
    _require_admin()
    try:
        since_minutes = int(request.args.get("since_minutes", 60))
    except (TypeError, ValueError):
        since_minutes = 60

    try:
        from delivery_ops_command_org import run_pass
        summary = run_pass(since_minutes=since_minutes)
    except Exception as e:
        log.warning(f"admin_run_pass failed: {e}")
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"ok": False, "error": str(e)}), 500
        flash(f"run_pass failed: {e}", "danger")
        return redirect(url_for("delivery_ops_command.admin_dashboard"))

    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True, "summary": summary})

    flash(
        f"run_pass done: {summary.get('jobs_seen', 0)} jobs scanned, "
        f"{summary.get('queued', 0)} queued, "
        f"{summary.get('delivered_within_sla', 0)} delivered within SLA, "
        f"{summary.get('delivered_sla_breach', 0)} SLA breaches, "
        f"{summary.get('failed_qc', 0)} failed QC, "
        f"{summary.get('rejected_capacity', 0)} rejected (capacity), "
        f"{summary.get('skipped_idempotent', 0)} skipped (idempotent).",
        "success",
    )
    return redirect(url_for("delivery_ops_command.admin_dashboard"))


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def register_routes(app) -> None:
    """Register the Delivery Ops Command blueprint. Idempotent — skip if
    already registered (mirrors acquisition_studio_routes pattern).
    """
    if "delivery_ops_command" in app.blueprints:
        log.info("delivery_ops_command blueprint already registered; skipping.")
        return
    app.register_blueprint(delivery_ops_bp)
    log.info(
        "delivery_ops_command blueprint registered: "
        "/ai_org/admin/delivery_ops_command[.json], "
        "/ai_org/admin/delivery_ops_command/run_pass"
    )


__all__ = ["delivery_ops_bp", "register_routes"]
