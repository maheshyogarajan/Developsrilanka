"""
AI-Org Score routes — Subagent C (2026-05-18).

Public-ish leaderboard (bands only) + admin breakdown + dispute API stub.

Routes:
  GET  /ai_org/leaderboard          — PUBLIC, no auth; bands only (council line 43)
  GET  /admin/ai_org/scores         — admin breakdown table
  GET  /admin/ai_org/scores.json    — same as JSON
  POST /admin/ai_org/scores/recompute — manual recompute trigger
  POST /ai_org/<slug>/dispute       — login_required; org can challenge its score

Admin gate: same @login_required + inline role=='admin' abort(403) pattern
used by ai_org_audit_routes / customer_brain_routes.

Wiring: orchestrator calls register_routes(app) at app init.
"""
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

ai_org_score_bp = Blueprint(
    "ai_org_score",
    __name__,
    url_prefix="/ai_org",
)


def _require_admin():
    """Raise 403 if current_user is not admin. login_required upstream handles
    the unauthenticated case (redirects to /login)."""
    role = getattr(current_user, "role", None)
    if role != "admin":
        abort(403)


# --------------------------------------------------------------------------- #
# PUBLIC: leaderboard (bands only)
# --------------------------------------------------------------------------- #

@ai_org_score_bp.route("/leaderboard", methods=["GET"])
def leaderboard():
    """PUBLIC: no auth. Shows slug + name + band ONLY. No raw scores, no axis
    breakdown — council line 43: "externally exposed as org-level bands only,
    never raw individual numbers — prevents copy-trading + speculation
    behaviour Olas/Bittensor suffer from."
    """
    try:
        from ai_org_models import AIOrg
        orgs = (
            AIOrg.query.filter_by(status="active")
            .order_by(AIOrg.slug.asc())
            .all()
        )
        # Project to band-only shape — defence in depth in case template
        # accidentally references org.status_score.
        rows = [
            {
                "slug": o.slug,
                "name": o.name,
                "band": o.status_band or "C",
                "last_computed_at": o.last_score_computed_at,
            }
            for o in orgs
        ]
    except Exception as e:
        log.warning(f"leaderboard query failed: {e}")
        rows = []
    return render_template("ai_org/leaderboard.html", rows=rows)


# --------------------------------------------------------------------------- #
# ADMIN: full breakdown
# --------------------------------------------------------------------------- #

def _admin_breakdown_rows():
    """Build the full breakdown rows used by both the HTML and JSON admin views."""
    from sqlalchemy import text as sql_text
    from app import db
    from ai_org_models import AIOrg

    orgs = (
        AIOrg.query.filter_by(status="active")
        .order_by(AIOrg.status_score.desc(), AIOrg.slug.asc())
        .all()
    )
    if not orgs:
        return []

    # Count of reputation events per org in last 90d — one query, in-memory join.
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)
    rep_counts = {}
    try:
        result = db.session.execute(
            sql_text(
                "SELECT ai_org_id, COUNT(*) FROM reputation_event "
                "WHERE occurred_at >= :cutoff GROUP BY ai_org_id"
            ),
            {"cutoff": ninety_days_ago},
        ).fetchall()
        rep_counts = {int(oid): int(c) for oid, c in result}
    except Exception as e:
        log.warning(f"_admin_breakdown_rows rep_counts query failed: {e}")

    out = []
    for o in orgs:
        out.append({
            "id": o.id,
            "slug": o.slug,
            "name": o.name,
            "economic_axis": float(o.economic_axis) if o.economic_axis is not None else None,
            "human_impact_axis": float(o.human_impact_axis) if o.human_impact_axis is not None else None,
            "ai_reliability_axis": float(o.ai_reliability_axis) if o.ai_reliability_axis is not None else None,
            "composite": float(o.status_score) if o.status_score is not None else None,
            "band": o.status_band,
            "last_computed_at": (
                o.last_score_computed_at.isoformat()
                if o.last_score_computed_at else None
            ),
            "rep_events_90d": rep_counts.get(o.id, 0),
        })
    return out


@ai_org_score_bp.route("/admin/scores", methods=["GET"], endpoint="admin_scores")
@login_required
def admin_scores():
    """Admin-only full score breakdown."""
    _require_admin()
    rows = _admin_breakdown_rows()
    # Audit metrics — informs the operator whether the confidence multiplier
    # would currently apply (Subagent B handoff).
    audit_meta = None
    try:
        from ai_org_audit_harness import audit_metrics
        audit_meta = audit_metrics()
    except Exception as e:
        log.info(f"admin_scores: audit_metrics unavailable ({e})")
    return render_template(
        "admin/ai_org_scores.html",
        rows=rows,
        audit_meta=audit_meta,
    )


@ai_org_score_bp.route("/admin/scores.json", methods=["GET"], endpoint="admin_scores_json")
@login_required
def admin_scores_json():
    """Admin-only JSON variant for tooling."""
    _require_admin()
    return jsonify({
        "orgs": _admin_breakdown_rows(),
        "as_of": datetime.utcnow().isoformat(),
    })


@ai_org_score_bp.route(
    "/admin/scores/recompute",
    methods=["POST"],
    endpoint="admin_scores_recompute",
)
@login_required
def admin_scores_recompute():
    """Admin-only: trigger an out-of-band recompute. Runs synchronously in the
    request (fast — only 3-5 orgs at MVP). Returns a flash message + redirect."""
    _require_admin()
    try:
        from ai_org_score_engine import recompute_all_orgs
        summary = recompute_all_orgs()
        flash(
            f"Recompute done: {summary.get('orgs_scored', 0)} orgs scored.",
            "success",
        )
    except Exception as e:
        log.warning(f"admin_scores_recompute failed: {e}")
        flash(f"Recompute failed: {e}", "danger")
    return redirect(url_for("ai_org_score.admin_scores"))


# --------------------------------------------------------------------------- #
# DISPUTE API STUB — council mitigation #1
# --------------------------------------------------------------------------- #

@ai_org_score_bp.route(
    "/<slug>/dispute",
    methods=["POST"],
    endpoint="file_dispute",
)
@login_required
def file_dispute(slug: str):
    """File a score dispute for org `slug`. Login required (any user, NOT
    admin-only — the orgs themselves can dispute their own scores).

    Council mitigation #1: "publish nightly score-computation diffs; allow
    external orgs to challenge their score via an /score/dispute API with
    red-team review."

    Body (JSON): {reason: str, evidence_payload: object}
    Returns: 202 Accepted with dispute_id, or 4xx on input error.
    """
    from app import db
    from ai_org_models import AIOrg
    from ai_org_score_engine import ScoreDispute

    org = AIOrg.query.filter_by(slug=slug).first()
    if org is None:
        return jsonify({"ok": False, "error": "org not found"}), 404

    data = request.get_json(silent=True) or request.form or {}
    reason = (data.get("reason") or "").strip()
    evidence = data.get("evidence_payload")
    if not reason:
        return jsonify({"ok": False, "error": "reason is required"}), 400

    try:
        d = ScoreDispute(
            ai_org_id=org.id,
            filed_by_user_id=current_user.id,
            reason=reason,
            evidence_payload=evidence if isinstance(evidence, (dict, list)) else None,
            status="open",
        )
        db.session.add(d)
        db.session.commit()
        dispute_id = d.id
    except Exception as e:
        log.warning(f"file_dispute insert failed: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": "insert failed"}), 500

    # Best-effort event emission for downstream Strategy Council review queue.
    try:
        from events import emit as emit_event
        emit_event(
            event_type="ai_org_score_disputed",
            user_id=current_user.id,
            payload={
                "dispute_id": dispute_id,
                "ai_org_id": org.id,
                "ai_org_slug": org.slug,
                "reason": reason[:512],
            },
            source="ai_org_score_routes.file_dispute",
        )
    except Exception as e:
        log.info(f"ai_org_score_disputed event emit skipped: {e}")

    return jsonify({
        "ok": True,
        "dispute_id": dispute_id,
        "status": "open",
        "ai_org_slug": org.slug,
        "message": "Dispute filed; Strategy Council review pending.",
    }), 202


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def register_routes(app) -> None:
    """Register the Score Engine blueprint on the Flask app.

    Idempotent: skip if already registered (mirrors revenue_intel /
    customer_brain_routes pattern).
    """
    if "ai_org_score" in app.blueprints:
        log.info("ai_org_score blueprint already registered; skipping.")
        return
    app.register_blueprint(ai_org_score_bp)
    log.info("ai_org_score blueprint registered: /ai_org/leaderboard, "
             "/ai_org/admin/scores[.json], /ai_org/admin/scores/recompute, "
             "/ai_org/<slug>/dispute")


__all__ = ["ai_org_score_bp", "register_routes"]
