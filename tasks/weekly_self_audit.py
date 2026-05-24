"""
tasks/weekly_self_audit.py — Weekly self-audit beat task (Tier D4 / E5).

WHAT
----
Runs once a week (Mon 03:30 UTC = 09:00 IST) and POSTs the report from
``self_audit.generate_weekly_report()`` to the CEO's Telegram via
``ops_alerts.send_alert``.

Schedule lives in ``celery_config.app.conf.beat_schedule``; see the
``weekly-self-audit-mon-0330-utc`` entry there.

WHY 09:00 IST MON
-----------------
- 09:00 IST = start of CEO's work day; the report frames the week.
- Mon = post-weekend, so revenue / signup data captures the full prior week
  including weekend baselines.
- 03:30 UTC is off-cycle from every other scheduled task in the worker
  (ai_crm 02:00, ai_org_score_engine 03:00, faq_autogen Mon 04:00,
  cbsl_rate_fetch 07:30, lankatax_crosssell 07:00,
  signup_drop_probe 03:30 IST=22:00 UTC). The only neighbour is the
  ops signup-drop probe at 03:30 UTC — which has zero DB overlap with
  the audit (different table, different aggregation), so contention is
  not a concern.

WHY ONE SEND (NOT PER-SECTION)
------------------------------
The CEO wants to read ONE message and have the full picture. Per-section
sends would defeat that AND would multiply ops_alerts dedup-window churn.

NEVER RAISES
------------
The task is best-effort: a failure to build or send the report writes an
error log and returns ``{"sent": False, "reason": ...}`` so Celery doesn't
retry-loop on an unrecoverable error (e.g. DB down — the alert wouldn't
get through anyway).
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def run_weekly_audit_impl() -> dict:
    """Build the report and ship it to Telegram. Returns a status dict.

    Pulled out of the @celery_app.task wrapper so unit tests can call it
    directly without spinning up a worker.
    """
    try:
        from self_audit import generate_weekly_report
        report = generate_weekly_report()
    except Exception as e:
        log.exception("weekly_self_audit: report generation failed")
        return {"sent": False, "report_built": False, "reason": f"build_error: {e}"}

    try:
        from ops_alerts import send_alert
        result = send_alert(
            severity="INFO",
            title="FIESTA Weekly Self-Audit",
            body=report,
        )
    except Exception as e:
        log.exception("weekly_self_audit: send_alert failed")
        return {
            "sent": False,
            "report_built": True,
            "report_chars": len(report),
            "reason": f"send_error: {e}",
        }

    return {
        "sent": bool(result.get("sent")),
        "deduped": bool(result.get("deduped")),
        "report_built": True,
        "report_chars": len(report),
        "reason": result.get("reason"),
    }


# --------------------------------------------------------------------------- #
# Celery task wiring (same idiom as tasks/ops_probes.py)
# --------------------------------------------------------------------------- #

try:
    from celery_config import app as celery_app

    @celery_app.task(name="tasks.weekly_self_audit.run_weekly_audit")
    def run_weekly_audit() -> dict:
        return run_weekly_audit_impl()
except Exception:
    # Celery not importable in test / CLI contexts — _impl is still callable.
    run_weekly_audit = None  # type: ignore[assignment]


__all__ = ["run_weekly_audit", "run_weekly_audit_impl"]
