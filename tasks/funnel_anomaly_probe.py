"""tasks/funnel_anomaly_probe.py — Celery wrapper for Tier D4 E3 funnel anomaly probe.

Wraps funnel_anomaly.run_daily_probe(db, send_alert_fn) in a Flask app
context so the SQLAlchemy session has a bound engine. Runs daily at
04:00 UTC (see celery_config.app.conf.beat_schedule).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def daily_probe_impl() -> dict:
    """Run the 5-metric funnel anomaly probe inside an app context.
    Returns a summary dict; never raises (errors are swallowed + logged).
    """
    summary: dict = {"ok": False, "results": None, "error": None}
    try:
        from app import app, db
        from ops_alerts import send_alert
        from funnel_anomaly import run_daily_probe

        with app.app_context():
            results = run_daily_probe(db, send_alert)
        summary["ok"] = True
        summary["results"] = results
        summary["anomalies"] = sum(1 for r in results if r.get("is_anomaly"))
    except Exception as e:
        log.exception("funnel_anomaly_probe.daily_probe failed")
        summary["error"] = str(e)
    return summary


try:
    from celery_config import app as celery_app

    @celery_app.task(name="tasks.funnel_anomaly_probe.daily_probe")
    def daily_probe() -> dict:
        return daily_probe_impl()
except Exception:
    daily_probe = None  # type: ignore


__all__ = ["daily_probe", "daily_probe_impl"]
