"""
tasks/lifecycle_drip_send.py — Tier D4 / A5: lifecycle drip Celery worker.

Scheduled every 15 minutes by celery_config.beat_schedule. Calls
lifecycle_drip.scan_and_send() to flush every pending row whose
scheduled_at has passed.

Wrapped in flask_app.app_context() to be defence-in-depth even though
celery_config v18.1 now pushes a context per-task.
"""
from __future__ import annotations

import logging

from celery_config import app as celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="tasks.lifecycle_drip_send.scan_and_send_task")
def scan_and_send_task(limit: int = 200) -> dict:
    """Beat-driven entry. Returns the counts dict for visibility."""
    try:
        from app import app as flask_app
        with flask_app.app_context():
            from lifecycle_drip import scan_and_send
            counts = scan_and_send(limit=limit)
            log.info(
                "lifecycle_drip_send: scanned=%d sent=%d skipped=%d failed=%d",
                counts.get("scanned", 0),
                counts.get("sent", 0),
                counts.get("skipped", 0),
                counts.get("failed", 0),
            )
            return counts
    except Exception as exc:
        log.exception("lifecycle_drip_send: task failed: %s", exc)
        return {"error": str(exc)[:512]}
