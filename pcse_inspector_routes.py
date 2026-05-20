"""
S16 — Flask routes for /admin/pcse.

Registered against the existing FIESTA `app` (imported from app.py). Stays
in its own module so the parallel wave6/admin-middleware-s15 branch can be
merged into admin_routes.py without colliding with this file.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import jsonify, render_template, request
from flask_login import current_user

from app import app
import pcse_inspector

# TODO(integration): once wave6/admin-middleware-s15 ships, replace the local
# `admin_required` import with whatever it exposes (likely
# `from middleware.admin import admin_required` or similar). The shape of
# the decorator is identical, so the route bodies do not need to change.
from decorators import admin_required  # current canonical implementation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GET /admin/pcse — 4-tab inspector
# ---------------------------------------------------------------------------
@app.route("/admin/pcse")
@admin_required
def admin_pcse_inspector():
    state_filter = request.args.get("state") or None
    decision_limit = request.args.get("decision_limit", default=50, type=int)
    bucket_limit = request.args.get("bucket_limit", default=500, type=int)
    payload = pcse_inspector.build_inspector_payload(
        state_filter=state_filter,
        decision_limit=decision_limit,
        bucket_limit=bucket_limit,
    )
    return render_template(
        "admin/pcse_inspector.html",
        payload=payload,
        state_filter=state_filter or "",
        v1_states=pcse_inspector.V1_STATES,
        v2_states=pcse_inspector.V2_STATES,
        state_labels=pcse_inspector.STATE_LABELS,
        halt_confirm_text=pcse_inspector.HALT_CONFIRM_TEXT,
    )


# ---------------------------------------------------------------------------
# JSON refresh endpoints (per-tab refresh without full page reload)
# ---------------------------------------------------------------------------
@app.route("/admin/pcse/data/state-graph")
@admin_required
def admin_pcse_state_graph_data():
    try:
        counts = pcse_inspector.fetch_state_distribution()
        edges = pcse_inspector.fetch_transition_edges(min_probability=0.05)
        svg = pcse_inspector.build_state_graph_svg(counts, edges)
        return jsonify({
            "ok": True, "state_distribution": counts,
            "transition_edges": edges, "svg": svg,
        })
    except Exception as e:
        logger.exception("admin_pcse_state_graph_data error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/pcse/data/buckets")
@admin_required
def admin_pcse_buckets_data():
    try:
        state_filter = request.args.get("state") or None
        limit = request.args.get("limit", default=500, type=int)
        rows = pcse_inspector.fetch_active_buckets(
            state_filter=state_filter, limit=limit,
        )
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        logger.exception("admin_pcse_buckets_data error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/pcse/data/decisions")
@admin_required
def admin_pcse_decisions_data():
    try:
        limit = request.args.get("limit", default=50, type=int)
        rows = pcse_inspector.fetch_recent_decisions(limit=limit)
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        logger.exception("admin_pcse_decisions_data error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/pcse/data/engine-state")
@admin_required
def admin_pcse_engine_state_data():
    try:
        state = pcse_inspector.fetch_engine_state()
        return jsonify({"ok": True, "engine_state": state})
    except Exception as e:
        logger.exception("admin_pcse_engine_state_data error")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# POST control handlers
# ---------------------------------------------------------------------------
def _changed_by_for(user) -> str:
    """Best-effort attribution string for pcse_engine_state.changed_by."""
    try:
        if getattr(user, "is_authenticated", False):
            return f"admin:{user.id}"
    except Exception:
        pass
    return "admin:unknown"


@app.route("/admin/pcse/control/pause", methods=["POST"])
@admin_required
def admin_pcse_pause():
    try:
        reason = (request.form.get("reason")
                  or (request.get_json(silent=True) or {}).get("reason")
                  or "ceo_pause_via_admin_ui")
        state = pcse_inspector.pause_engine(
            reason=reason, changed_by=_changed_by_for(current_user),
        )
        return jsonify({"ok": True, "engine_state": state})
    except Exception as e:
        logger.exception("admin_pcse_pause error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/pcse/control/resume", methods=["POST"])
@admin_required
def admin_pcse_resume():
    try:
        reason = (request.form.get("reason")
                  or (request.get_json(silent=True) or {}).get("reason")
                  or "ceo_resume_via_admin_ui")
        state = pcse_inspector.resume_engine(
            reason=reason, changed_by=_changed_by_for(current_user),
        )
        return jsonify({"ok": True, "engine_state": state})
    except Exception as e:
        logger.exception("admin_pcse_resume error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/pcse/control/halt", methods=["POST"])
@admin_required
def admin_pcse_halt():
    """Emergency halt. Requires confirm_text == 'HALT' (Tier-2 confirmation)."""
    payload_body: Dict[str, Any] = request.get_json(silent=True) or {}
    confirm = (request.form.get("confirm_text")
               or payload_body.get("confirm_text")
               or "")
    if confirm != pcse_inspector.HALT_CONFIRM_TEXT:
        return jsonify({
            "ok": False,
            "error": (
                f"Halt requires confirm_text='{pcse_inspector.HALT_CONFIRM_TEXT}' "
                "(Tier-2 confirmation)."
            ),
        }), 400

    try:
        reason = (request.form.get("reason")
                  or payload_body.get("reason")
                  or "manual_halt_via_admin_ui")
        state = pcse_inspector.halt_engine(
            reason=reason, changed_by=_changed_by_for(current_user),
        )
        return jsonify({"ok": True, "engine_state": state})
    except Exception as e:
        logger.exception("admin_pcse_halt error")
        return jsonify({"ok": False, "error": str(e)}), 500
