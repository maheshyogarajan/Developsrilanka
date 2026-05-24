"""
tasks/secret_rotation_probe.py — Tier D5 / F4 secret rotation probe
(2026-05-24).

WHAT
----
Runs once a week (Mon 06:00 UTC = 11:30 IST) and invokes
``secret_rotation_check.check_and_alert()``, which:
  - Loads _tier_d5_secret_rotation/secret_inventory.yaml
  - Computes days_since_rotation per secret
  - Telegram-alerts CEO via ops_alerts.send_alert for any WARN (>=
    rotation_days - 15) or URGENT (>= rotation_days) secrets
  - One alert per bucket (not per secret) so the CEO sees a single
    consolidated reminder.

WHY 06:00 UTC MON
-----------------
06:00 UTC = 11:30 IST = mid-morning for the CEO, AFTER the existing
self-audit (Mon 03:35 UTC) so the two don't stack on the same alert
window. 06:00 UTC is also off-cycle from every other Celery beat task
(ai_crm 02:00, ai_org_score 03:00, signup_drop 03:30, weekly_self_audit
03:35, faq_autogen 04:00, funnel_anomaly 04:00, cbsl 07:30,
lankatax_crosssell 07:00).

WHY ONE BUCKET-LEVEL ALERT
--------------------------
The CEO wants ONE WARN message + ONE URGENT message per Monday — not 17
separate alerts when several secrets approach a window together. The
dedup window in ops_alerts is 10 min, so multi-send would also burn
through the dedup state unhelpfully.

NEVER RAISES
------------
Wrapped in try/except so a broken inventory YAML or a failing alert path
doesn't retry-loop the worker.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def run_secret_rotation_probe_impl() -> dict:
    """Build the rotation report and ship alerts. Returns a status dict.

    Pulled out of the @celery_app.task wrapper so tests can call it
    directly without spinning up a worker.
    """
    try:
        from secret_rotation_check import check_and_alert
    except Exception as e:
        log.exception("secret_rotation_probe: import failed")
        return {"sent": False, "reason": f"import_error: {e}"}

    try:
        result = check_and_alert()
    except Exception as e:
        log.exception("secret_rotation_probe: check_and_alert raised")
        return {"sent": False, "reason": f"check_error: {e}"}

    return {
        "sent": result.get("alerts_sent", 0) > 0,
        "checked": result.get("checked", 0),
        "ok": result.get("ok", 0),
        "warn": result.get("warn", 0),
        "urgent": result.get("urgent", 0),
        "alerts_sent": result.get("alerts_sent", 0),
        "parse_errors": result.get("parse_errors", []),
    }


# --------------------------------------------------------------------------- #
# Celery task wiring (same idiom as tasks/weekly_self_audit.py)
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    @celery_app.task(name="tasks.secret_rotation_probe.run_probe")
    def run_probe() -> dict:
        return run_secret_rotation_probe_impl()
except Exception:
    # Celery not importable in test / CLI contexts — _impl is still callable.
    run_probe = None  # type: ignore[assignment]


__all__ = ["run_probe", "run_secret_rotation_probe_impl"]
