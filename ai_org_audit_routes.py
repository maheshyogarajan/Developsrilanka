"""
AI-Org Audit admin routes — Subagent B (2026-05-18).

Three admin-only endpoints for the 50-sample human audit:
  GET  /admin/ai_org/audit/sample         — Bootstrap table of sample rows
  POST /admin/ai_org/audit/<int:id>       — apply confirm/reject/reassign
  GET  /admin/ai_org/audit/metrics.json   — aggregate metrics JSON

Admin gate: re-uses the @login_required + role=='admin' pattern already used
by other /admin blueprints (revenue_intel, customer_brain_routes). If the
current user is unauthenticated or not admin, returns 302→/login or 403.

Wiring: orchestrator calls register_routes(app) at app init.
"""
import logging

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    abort,
)
from flask_login import login_required, current_user

log = logging.getLogger(__name__)

ai_org_audit_bp = Blueprint(
    "ai_org_audit",
    __name__,
    url_prefix="/admin/ai_org/audit",
)


def _require_admin():
    """Raise 403 if current_user is not admin. login_required upstream handles
    the unauthenticated case (redirects to /login)."""
    role = getattr(current_user, "role", None)
    if role != "admin":
        abort(403)


@ai_org_audit_bp.route("/sample", methods=["GET"])
@login_required
def sample_view():
    """Render the 50-sample audit table."""
    _require_admin()
    from ai_org_audit_harness import sample_attributions_for_audit
    from ai_org_models import AIOrg

    n = int(request.args.get("n", 50))
    days_back = int(request.args.get("days_back", 7))
    rows = sample_attributions_for_audit(n=n, days_back=days_back)

    # All orgs — populated into the "reassign" dropdown.
    try:
        all_orgs = AIOrg.query.order_by(AIOrg.slug.asc()).all()
    except Exception:
        all_orgs = []

    # All red-team roles (the canonical verifier role pool) for the
    # verifier_role_id dropdown.
    try:
        from ai_org_models import AIOrgRole
        verifier_roles = (
            AIOrgRole.query
            .filter_by(is_red_team=True, is_active=True)
            .order_by(AIOrgRole.id.asc())
            .all()
        )
    except Exception:
        verifier_roles = []

    return render_template(
        "admin/ai_org_audit.html",
        rows=rows,
        all_orgs=all_orgs,
        verifier_roles=verifier_roles,
        n=n,
        days_back=days_back,
    )


@ai_org_audit_bp.route("/<int:attribution_id>", methods=["POST"])
@login_required
def submit_decision(attribution_id: int):
    """Receive a single-row decision form submission."""
    _require_admin()
    from ai_org_audit_harness import audit_decision

    decision = request.form.get("decision", "").strip()
    verifier_role_id_raw = request.form.get("verifier_role_id", "").strip()
    new_org_id_raw = request.form.get("new_org_id", "").strip()
    notes = request.form.get("notes", "").strip() or None

    try:
        verifier_role_id = int(verifier_role_id_raw) if verifier_role_id_raw else 0
    except ValueError:
        verifier_role_id = 0

    new_org_id_if_reassign = None
    if new_org_id_raw:
        try:
            new_org_id_if_reassign = int(new_org_id_raw)
        except ValueError:
            new_org_id_if_reassign = None

    result = audit_decision(
        attribution_id=attribution_id,
        decision=decision,
        verifier_role_id=verifier_role_id,
        new_org_id_if_reassign=new_org_id_if_reassign,
        notes=notes,
    )
    log.info(f"audit_decision result: {result}")

    return redirect(url_for("ai_org_audit.sample_view"))


@ai_org_audit_bp.route("/metrics.json", methods=["GET"])
@login_required
def metrics_json():
    """Return the aggregate audit metrics as JSON."""
    _require_admin()
    from ai_org_audit_harness import audit_metrics
    return jsonify(audit_metrics())


def register_routes(app) -> None:
    """Orchestrator entry point. Idempotent — guarded against double-registration."""
    if "ai_org_audit" in app.blueprints:
        return
    app.register_blueprint(ai_org_audit_bp)
