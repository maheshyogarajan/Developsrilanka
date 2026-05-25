"""fiesta.admin.cron_routes — admin-triggered fallback endpoints for scheduled jobs.

D13 (Tier-D6 minor, 2026-05-25)
-------------------------------
Celery beat is configured for ``tasks.cbsl_rate_fetch.fetch_today_task`` at
07:30 UTC daily (see celery_config.py beat_schedule). In MS1 staging the beat
process was observed not running reliably (worker only, no beat container), so
``cbsl_rates`` carried stale rows and the public estimator surfaced
``source: "manual"`` / ``is_ird_defensible: false``.

This module provides admin-only manual-trigger endpoints so an operator can
populate today's CBSL cache without needing to wait for the scheduler or
shell into the worker. The Flask CLI ``flask cbsl-fetch`` and the Celery beat
schedule are unchanged — this is a third, manual path.

Endpoints
---------
* ``POST /admin/cron/cbsl_rate_fetch_now`` — execute fetch_and_cache(today)
  inline, return JSON summary.
* ``GET /admin/cron/cbsl_rate_fetch_now`` — render a tiny HTML form (CSRF
  token + submit button). Lets an admin trigger via a normal browser.

Auth: ``admin_required`` decorator (role='admin') wraps both. Non-admin
authenticated users get a 403; anonymous users redirect to login.
"""
from __future__ import annotations

import logging
from datetime import date

from flask import Blueprint, jsonify, render_template_string, request

from fiesta.auth.decorators import admin_required

log = logging.getLogger(__name__)

fiesta_admin_cron_bp = Blueprint(
    "fiesta_admin_cron",
    __name__,
)


_TRIGGER_FORM = """
<!doctype html>
<html><head><title>Admin · Cron triggers</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 1.4rem; color: #1f3a2d; }
  .card { border: 1px solid #d8cab4; background: #f7f5f0; padding: 1.2rem; border-radius: 10px; margin-top: 1rem; }
  button { background: #B8542F; color: #fff; border: 0; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600; }
  button:hover { background: #8e3f22; }
  .meta { font-family: 'JetBrains Mono', monospace; font-size: .8rem; color: #6b6f5e; }
</style></head>
<body>
  <h1>Cron triggers (manual fallback)</h1>
  <p>Use these when Celery beat is offline or you need to force a job to run now. Each click runs the job inline in the request thread.</p>
  <div class="card">
    <h2>CBSL daily rate fetch</h2>
    <p class="meta">Endpoint: <code>POST /admin/cron/cbsl_rate_fetch_now</code><br>
    Scheduled: <code>tasks.cbsl_rate_fetch.fetch_today_task</code> @ 07:30 UTC daily</p>
    <form method="post" action="/admin/cron/cbsl_rate_fetch_now">
      <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
      <button type="submit" data-testid="cron-cbsl-trigger">Run CBSL fetch now</button>
    </form>
  </div>
</body></html>
"""


@fiesta_admin_cron_bp.route("/admin/cron/cbsl_rate_fetch_now", methods=["GET"])
@admin_required
def cbsl_rate_fetch_form():
    """Render the admin-trigger form (one button)."""
    return render_template_string(_TRIGGER_FORM)


@fiesta_admin_cron_bp.route("/admin/cron/cbsl_rate_fetch_now", methods=["POST"])
@admin_required
def cbsl_rate_fetch_now():
    """Trigger today's CBSL fetch inline. Returns JSON summary regardless of
    success/failure (the underlying ``fetch_and_cache`` is defensive — it never
    raises). Always exits 200; the body's ``cache_written`` count is the
    success signal.
    """
    try:
        from tasks.cbsl_rate_fetch import fetch_and_cache
    except Exception as exc:  # noqa: BLE001
        log.exception("cron trigger: cbsl_rate_fetch import failed")
        return jsonify({
            "ok": False,
            "error": f"import failed: {exc}",
            "trigger": "admin_manual",
        }), 500

    target = date.today()
    summary = fetch_and_cache(target)
    summary["trigger"] = "admin_manual"
    summary["ok"] = summary.get("cache_written", 0) > 0
    log.info(
        "cron trigger: cbsl_rate_fetch_now date=%s written=%d skipped=%d failed=%d",
        target,
        summary.get("cache_written", 0),
        len(summary.get("skipped", [])),
        len(summary.get("failed", [])),
    )
    return jsonify(summary)


def register_routes(app) -> None:
    """Register the cron trigger blueprint. Idempotent."""
    if "fiesta_admin_cron" in app.blueprints:
        return
    app.register_blueprint(fiesta_admin_cron_bp)
    log.info("FIESTA admin cron-trigger blueprint registered: /admin/cron/*")


__all__ = ["fiesta_admin_cron_bp", "register_routes"]
