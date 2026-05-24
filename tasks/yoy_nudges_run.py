"""
tasks/yoy_nudges_run.py — Celery task wrappers for YoY nudges (Tier D4 / C2).

Three beat-driven schedulers + a daily renewal-check + a dispatcher. All run
inside the Flask app_context pushed by the celery_config v18.1 signal handler.

Tasks:
    yoy_nudges.apr_1_run        — Apr 1 04:00 UTC yearly
    yoy_nudges.payment_run      — Sep 1 04:00 UTC yearly
    yoy_nudges.filing_run       — Oct 31 04:00 UTC yearly
    yoy_nudges.renewal_check    — every day 04:30 UTC
    yoy_nudges.dispatch         — every hour at :12 (drains the queue)

Splitting schedule from dispatch lets us:
    1. Test the audience queries deterministically.
    2. Let the dispatcher idle-walk an empty queue cheaply on most hours,
       and only do work after the schedulers populate it.
    3. Swap the stubbed send for a live SendGrid path without touching
       the beat schedule.
"""
from __future__ import annotations

import logging

from celery_config import app

log = logging.getLogger(__name__)


def _ensure_yoy_model():
    """Register the YoYNudge model against the live db. Idempotent."""
    try:
        from yoy_models import register_models
        register_models()
        # Be defensive: create_all here too, so a worker that booted before
        # main.py ran (unusual but possible) still has the table.
        from app import db
        db.create_all()
    except Exception as exc:
        log.warning("yoy_nudges_run._ensure_yoy_model: %s", exc)


@app.task(name="yoy_nudges.apr_1_run")
def apr_1_run() -> dict:
    """Fired by beat on Apr 1 yearly."""
    _ensure_yoy_model()
    from yoy_nudges import schedule_apr_1_nudges
    return schedule_apr_1_nudges()


@app.task(name="yoy_nudges.payment_run")
def payment_run() -> dict:
    """Fired by beat on Sep 1 yearly (30d before Sep 30 deadline)."""
    _ensure_yoy_model()
    from yoy_nudges import schedule_payment_deadline_nudges
    return schedule_payment_deadline_nudges()


@app.task(name="yoy_nudges.filing_run")
def filing_run() -> dict:
    """Fired by beat on Oct 31 yearly (30d before Nov 30 deadline)."""
    _ensure_yoy_model()
    from yoy_nudges import schedule_filing_deadline_nudges
    return schedule_filing_deadline_nudges()


@app.task(name="yoy_nudges.renewal_check")
def renewal_check() -> dict:
    """Fired by beat daily — checks subscriptions expiring in <=30d."""
    _ensure_yoy_model()
    from yoy_nudges import schedule_renewal_nudges
    return schedule_renewal_nudges()


@app.task(name="yoy_nudges.dispatch")
def dispatch() -> dict:
    """Fired by beat hourly — drains scheduled rows via the stub send."""
    _ensure_yoy_model()
    from yoy_nudges import dispatch_pending
    return dispatch_pending()


__all__ = [
    "apr_1_run",
    "payment_run",
    "filing_run",
    "renewal_check",
    "dispatch",
]
