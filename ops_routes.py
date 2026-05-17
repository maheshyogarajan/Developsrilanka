"""
Internal ops routes — admin-only health + cost dashboards (Wave 2.4, 2026-05-17).

Two endpoints, both under /internal/ops/ + admin_required:

    GET /internal/ops/health        -> JSON snapshot from run_all_checks()
    GET /internal/ops/cost-summary  -> Gemini spend rollups for the last 24h

These are NOT public — they read like operational dashboards but expose
infra signals (Neon, Fly, Celery queue depth) that would be leak-worthy if
left open. Wrapped in decorators.admin_required which already redirects
non-admin users to / with a flash.

Pairs with:
  * ops_sentinel.run_all_checks   (powers /health)
  * gemini_cost_log_model         (powers /cost-summary)
  * main.py register_routes wiring (orchestrator wires this in)
"""
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify

from decorators import admin_required

log = logging.getLogger(__name__)

ops_bp = Blueprint("ops", __name__, url_prefix="/internal/ops")


# --------------------------------------------------------------------------- #
# /internal/ops/health — JSON snapshot for CEO + external monitoring poll
# --------------------------------------------------------------------------- #

@ops_bp.route("/health", methods=["GET"])
@admin_required
def health():
    """Returns the run_all_checks() snapshot as JSON.

    Intended consumers:
      * CEO ad-hoc browser check
      * External monitoring (UptimeRobot, Better Stack) polling on a 1-min
        cadence — they'll get the same snapshot the 5-min Celery beat sees
      * /internal/ops/cost-summary call chain ("am I being throttled?")
    """
    from ops_sentinel import run_all_checks
    snapshot = run_all_checks()
    # Status code reflects overall health so monitoring tools that key off
    # HTTP status (not JSON body) catch alerts too.
    status_code = 200 if snapshot["overall_healthy"] else 503
    return jsonify(snapshot), status_code


# --------------------------------------------------------------------------- #
# /internal/ops/cost-summary — Gemini spend rollups for the last 24h
# --------------------------------------------------------------------------- #

@ops_bp.route("/cost-summary", methods=["GET"])
@admin_required
def cost_summary():
    """Returns rollups over GeminiCostLog for the last 24h:

        {
          "window": "24h",
          "ran_at": "<iso>",
          "last_24h_usd": <decimal>,
          "row_count_24h": <int>,
          "by_model":      {"<model_name>": "<usd_total>", ...},
          "by_source":     {"<source>":     "<usd_total>", ...},
          "by_user_top_10":[{"user_id": ..., "usd": ..., "rows": ...}, ...]
        }

    USD values are stringified Decimal to preserve precision through JSON
    (jsonify renders Decimal as a string by default, but we do it explicitly
    so the shape doesn't depend on Flask config).
    """
    from sqlalchemy import text
    from app import db

    cutoff = datetime.utcnow() - timedelta(hours=24)
    ran_at = datetime.utcnow().isoformat()

    out = {
        "window": "24h",
        "ran_at": ran_at,
        "last_24h_usd": "0",
        "row_count_24h": 0,
        "by_model": {},
        "by_source": {},
        "by_user_top_10": [],
    }

    try:
        total_row = db.session.execute(text("""
            SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total, COUNT(*) AS rows
              FROM gemini_cost_log
             WHERE created_at >= :cutoff
        """), {"cutoff": cutoff}).fetchone()
        if total_row:
            total = Decimal(total_row[0]) if total_row[0] is not None else Decimal("0")
            out["last_24h_usd"] = f"{total:.6f}"
            out["row_count_24h"] = int(total_row[1] or 0)

        by_model_rows = db.session.execute(text("""
            SELECT model_name, COALESCE(SUM(estimated_cost_usd), 0) AS total
              FROM gemini_cost_log
             WHERE created_at >= :cutoff
          GROUP BY model_name
          ORDER BY total DESC
        """), {"cutoff": cutoff}).fetchall()
        out["by_model"] = {
            (r[0] or "unknown"): f"{Decimal(r[1] or 0):.6f}"
            for r in by_model_rows
        }

        by_source_rows = db.session.execute(text("""
            SELECT source, COALESCE(SUM(estimated_cost_usd), 0) AS total
              FROM gemini_cost_log
             WHERE created_at >= :cutoff
          GROUP BY source
          ORDER BY total DESC
        """), {"cutoff": cutoff}).fetchall()
        out["by_source"] = {
            (r[0] or "unknown"): f"{Decimal(r[1] or 0):.6f}"
            for r in by_source_rows
        }

        top_users = db.session.execute(text("""
            SELECT user_id,
                   COALESCE(SUM(estimated_cost_usd), 0) AS total,
                   COUNT(*) AS rows
              FROM gemini_cost_log
             WHERE created_at >= :cutoff
               AND user_id IS NOT NULL
          GROUP BY user_id
          ORDER BY total DESC
             LIMIT 10
        """), {"cutoff": cutoff}).fetchall()
        out["by_user_top_10"] = [
            {
                "user_id": int(r[0]),
                "usd": f"{Decimal(r[1] or 0):.6f}",
                "rows": int(r[2]),
            }
            for r in top_users
        ]
    except Exception as exc:
        # Best-effort: report what we have plus an error annotation. Don't
        # 500 because the dashboard is read-only and the caller (a human
        # admin) benefits from seeing partial data + the failure reason.
        log.warning("/internal/ops/cost-summary failed: %s", exc)
        out["error"] = str(exc)

    return jsonify(out), 200


# --------------------------------------------------------------------------- #
# register_routes — called by main.py
# --------------------------------------------------------------------------- #

def register_routes(app):
    app.register_blueprint(ops_bp)
    log.info("Ops routes registered at /internal/ops/* (admin-only)")
