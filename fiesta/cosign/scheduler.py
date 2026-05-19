"""fiesta.cosign.scheduler -- daily reminder pass for S10 co-sign workflows.

Wave 3 (2026-05-20). Per S10 dispatch brief.

Public entrypoint
-----------------
run_reminder_pass(now: datetime | None = None) -> dict

Behaviour
---------
1. Query all CosignWorkflow rows with status IN (sent_to_sp, sp_viewed,
   sp_signed).
2. For each: compute reminders_due() based on elapsed time since
   customer_email_sent_at (for SP-side reminders) or sp_signed_at (for
   customer countersign nudges).
3. Skip any reminder kind we've already fired (CosignReminder row exists
   for that workflow + kind).
4. Fire the appropriate email + log a CosignReminder row with status.
5. At T+14d unresolved: emit Telegram alert to CEO and mark
   workflow.ceo_escalated_at so we don't keep alerting.

Failure mode
------------
Per workflow row, exceptions are caught + logged. One bad row never
halts the whole pass. The return dict summarises what happened so the
Celery task can log + alert on it.

Wiring
------
Schedule via celery_config.py beat schedule:

    "cosign-daily-reminders": {
        "task": "fiesta.cosign.scheduler.run_reminder_pass_task",
        "schedule": crontab(hour=9, minute=0),  # 09:00 UTC daily
    },
"""
from __future__ import annotations

import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# How long since SP signed before we nudge the customer to countersign.
COUNTERSIGN_NUDGE_AFTER_HOURS = 24
# CEO Telegram chat ID for the T+14d escalation.
CEO_CHAT_ID = "1813046950"


# ---------------------------------------------------------------------------
# Telegram raw send -- duplicated minimal copy of worker_heartbeat._send_telegram
# so this module is self-contained.
# ---------------------------------------------------------------------------


def _resolve_telegram_token() -> Optional[str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token.strip()
    fly = Path("/etc/secrets/TELEGRAM_BOT_TOKEN")
    try:
        if fly.exists():
            v = fly.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:  # noqa: BLE001
        pass
    home_env = Path.home() / ".claude" / "channels" / "telegram" / ".env"
    try:
        if home_env.exists():
            for line in home_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip('"').strip("'")
    except Exception:  # noqa: BLE001
        pass
    return None


def _telegram_alert(text: str) -> bool:
    """Best-effort. Never raises."""
    token = _resolve_telegram_token()
    if not token:
        logger.warning("cosign scheduler: no telegram token, skipping alert")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": CEO_CHAT_ID, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        logger.warning("cosign scheduler: telegram POST failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Reminder pass
# ---------------------------------------------------------------------------


def _already_fired(workflow_id: int, kind: str) -> bool:
    """Has a CosignReminder of this kind already fired for this workflow?"""
    from fiesta.cosign.models import CosignReminder

    return (
        CosignReminder.query.filter_by(
            workflow_id=workflow_id, kind=kind
        ).first()
        is not None
    )


def _record_reminder(workflow_id: int, kind: str, ok: bool, status_msg: str):
    from fiesta.cosign.models import CosignReminder
    from app import db

    rec = CosignReminder(
        workflow_id=workflow_id,
        kind=kind,
        sendgrid_status="ok" if ok else "failed",
        error_message=None if ok else (status_msg or "")[:4000],
    )
    try:
        db.session.add(rec)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign scheduler: reminder log failed: %s", exc)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass


def _process_sp_reminders(workflow, now: datetime) -> list[dict]:
    """Fire SP-side reminders (first_3d, second_7d, escalate_14d) as due."""
    from fiesta.cosign.models import reminders_due
    from fiesta.cosign.email_sender import send_cosign_email
    from fiesta.agreements.models import ServiceAgreement
    from app import db

    fired: list[dict] = []
    if workflow.is_terminal:
        return fired
    if workflow.status not in ("sent_to_sp", "sp_viewed"):
        return fired
    if not workflow.sp_email:
        return fired

    for kind in reminders_due(workflow, now=now):
        if _already_fired(workflow.id, kind):
            continue

        agreement = ServiceAgreement.query.filter_by(
            id=workflow.service_agreement_id
        ).first()
        if not agreement:
            logger.warning(
                "cosign scheduler: workflow %s has no agreement, skipping",
                workflow.id,
            )
            continue

        email_kind = {
            "first_3d": "first_reminder",
            "second_7d": "second_reminder",
            "escalate_14d": "escalate",
        }.get(kind, "first_reminder")

        ok, status_msg = send_cosign_email(
            kind=email_kind, workflow=workflow, agreement=agreement
        )
        _record_reminder(workflow.id, kind, ok, status_msg)

        workflow.last_reminder_at = now
        workflow.reminder_count = (workflow.reminder_count or 0) + 1
        try:
            db.session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("cosign scheduler: workflow update failed: %s", exc)
            db.session.rollback()

        # Telegram escalation on the T+14d kind.
        if kind == "escalate_14d" and not workflow.ceo_escalated_at:
            alert = (
                "FIESTA S10: co-sign workflow %s stalled 14+ days. "
                "Customer user_id=%s, SP=%s, agreement_ref=%s. "
                "Manual intervention may be needed."
            ) % (
                workflow.id,
                workflow.user_id,
                workflow.sp_email,
                agreement.reference_id,
            )
            _telegram_alert(alert)
            workflow.ceo_escalated_at = now
            try:
                db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()

        fired.append(
            {
                "workflow_id": workflow.id,
                "kind": kind,
                "ok": ok,
                "status": status_msg,
            }
        )

    return fired


def _process_customer_countersign_nudge(workflow, now: datetime) -> Optional[dict]:
    """If SP signed > 24h ago and customer hasn't countersigned, nudge them."""
    from fiesta.cosign.email_sender import remind_customer_to_countersign
    from fiesta.agreements.models import ServiceAgreement

    if workflow.status != "sp_signed":
        return None
    if not workflow.sp_signed_at:
        return None
    elapsed = now - workflow.sp_signed_at
    if elapsed < timedelta(hours=COUNTERSIGN_NUDGE_AFTER_HOURS):
        return None
    if _already_fired(workflow.id, "countersign_nudge"):
        return None

    agreement = ServiceAgreement.query.filter_by(
        id=workflow.service_agreement_id
    ).first()
    if not agreement:
        return None

    ok, status_msg = remind_customer_to_countersign(
        workflow=workflow, agreement=agreement
    )
    _record_reminder(workflow.id, "countersign_nudge", ok, status_msg)
    return {
        "workflow_id": workflow.id,
        "kind": "countersign_nudge",
        "ok": ok,
        "status": status_msg,
    }


def run_reminder_pass(now: datetime | None = None) -> dict:
    """Single sweep. Returns a summary dict (workflow_count, reminders_fired,
    errors). Never raises -- per-row exceptions are caught + logged.
    """
    from fiesta.cosign.models import CosignWorkflow, IN_PROGRESS_STATUSES

    now = now or datetime.utcnow()
    summary = {
        "checked_at": now.isoformat(),
        "workflows_processed": 0,
        "reminders_fired": [],
        "errors": [],
    }

    try:
        rows = CosignWorkflow.query.filter(
            CosignWorkflow.status.in_(IN_PROGRESS_STATUSES)
        ).all()
    except Exception as exc:  # noqa: BLE001
        logger.error("cosign scheduler: query failed: %s", exc)
        summary["errors"].append({"phase": "query", "error": str(exc)})
        return summary

    for wf in rows:
        summary["workflows_processed"] += 1
        try:
            fired = _process_sp_reminders(wf, now)
            summary["reminders_fired"].extend(fired)
            nudge = _process_customer_countersign_nudge(wf, now)
            if nudge is not None:
                summary["reminders_fired"].append(nudge)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "cosign scheduler: workflow %s failed: %s", wf.id, exc
            )
            summary["errors"].append(
                {"workflow_id": wf.id, "error": str(exc)}
            )

    return summary


# ---------------------------------------------------------------------------
# Celery wrapper -- conditionally registered so test imports don't require celery
# ---------------------------------------------------------------------------


def _try_register_celery_task():
    try:
        from celery_config import celery  # type: ignore

        @celery.task(name="fiesta.cosign.scheduler.run_reminder_pass_task")
        def run_reminder_pass_task():
            return run_reminder_pass()

        return run_reminder_pass_task
    except Exception:  # noqa: BLE001
        return None


run_reminder_pass_task = _try_register_celery_task()


__all__ = ["run_reminder_pass", "run_reminder_pass_task"]
