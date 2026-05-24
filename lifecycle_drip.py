"""
lifecycle_drip.py — Tier D4 / A5: 5-email lifecycle drip sequence.

Council cap: 5 email keys total (welcome, calculator_nudge, payment_thanks,
sep30_30day, nov30_30day). EMAIL_KEYS in lifecycle_drip_models is the gate.

Public API
----------
  enroll(user, event)         schedules emails for a lifecycle event
                              ('signup' | 'payment_completed' |
                               'tax_year_cycle')
  compose(email_key, user,    returns {"subject": str, "html": str}
          context)
  send(lifecycle_email)       calls _send_stub() then writes sent_at +
                              send_status. Best-effort; never raises.
  scan_and_send(limit=200)    finds pending rows with scheduled_at<=now and
                              calls send() on each. Used by the beat task.

Email-send infra is STUBBED (FIESTA has no SES/Mailgun yet). The TODO at
bottom of file calls out the recommended next step.

Pattern mirrors dunning_sequence.py: late-bound model registration, ORM
imported lazily inside functions so Celery workers can import cleanly,
ops_alerts.send_alert for visibility, all errors logged + alerted but
never raise into the caller.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from flask import render_template

from lifecycle_drip_models import (
    EMAIL_KEYS,
    STATUS_PENDING,
    STATUS_SENT,
    STATUS_FAILED,
    STATUS_SKIPPED,
    LifecycleEmail,
    register_lifecycle_drip_model,
)

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Schedule helpers.
# --------------------------------------------------------------------------- #

def _cohort_for(dt: datetime) -> str:
    """Year-month cohort id ('2026-05'). Lets the same deadline reminder
    fire fresh in each new tax-year cycle without dedup collision."""
    return f"{dt.year:04d}-{dt.month:02d}"


def _next_deadline(target_month: int, target_day: int,
                   *, today: Optional[date] = None) -> date:
    """Return the next occurrence of `month-day` on/after today."""
    today = today or date.today()
    cand = date(today.year, target_month, target_day)
    if cand < today:
        cand = date(today.year + 1, target_month, target_day)
    return cand


def _schedule_for(email_key: str, event: str,
                  now: Optional[datetime] = None) -> Optional[datetime]:
    """Compute scheduled_at for one email_key based on the trigger event.

    Returns None if the (event, email_key) pair shouldn't enroll.
    """
    now = now or datetime.utcnow()

    if event == "signup":
        if email_key == "welcome":
            return now  # day 0, send immediately
        if email_key == "calculator_nudge":
            return now + timedelta(days=1)  # day 1 nudge
        return None

    if event == "payment_completed":
        if email_key == "payment_thanks":
            return now  # day 0 post-payment
        return None

    if event == "tax_year_cycle":
        # Schedule the next two deadline reminders.
        # Sep 30 = tax payment due. Nov 30 = tax return due. Reminder fires
        # 30 calendar days before each.
        if email_key == "sep30_30day":
            return datetime.combine(
                _next_deadline(9, 30) - timedelta(days=30),
                datetime.min.time(),
            )
        if email_key == "nov30_30day":
            return datetime.combine(
                _next_deadline(11, 30) - timedelta(days=30),
                datetime.min.time(),
            )
        return None

    return None


# --------------------------------------------------------------------------- #
# Calculator-interaction gate (for calculator_nudge dedupe at send time).
# --------------------------------------------------------------------------- #

def _user_has_calculated(user_id: int) -> bool:
    """True iff user has at least one 'estimator_run' event ever.

    Used by send() to skip the calculator_nudge if the user has since
    used the calculator (no nag after they engaged).
    """
    try:
        from event_models import Event
        return (
            Event.query
            .filter_by(user_id=user_id, event_type="estimator_run")
            .first()
            is not None
        )
    except Exception as exc:
        log.debug("_user_has_calculated lookup failed for u=%s: %s",
                  user_id, exc)
        return False  # fail-open: send the nudge


# --------------------------------------------------------------------------- #
# Enrolment.
# --------------------------------------------------------------------------- #

def enroll(user, event: str,
           now: Optional[datetime] = None) -> list[int]:
    """Schedule the relevant emails for a lifecycle event.

    Args:
        user:  User ORM row (.id, .email, .name).
        event: 'signup' | 'payment_completed' | 'tax_year_cycle'.

    Returns list of LifecycleEmail.id for rows created (or already-present
    rows whose dedup matched). Empty list on failure (never raises).
    """
    if not user or not getattr(user, "id", None) or not event:
        log.warning("enroll: missing user or event (user=%s, event=%s)",
                    user, event)
        return []
    if event not in {"signup", "payment_completed", "tax_year_cycle"}:
        log.warning("enroll: unknown event %r — ignoring", event)
        return []

    now = now or datetime.utcnow()
    cohort = _cohort_for(now)
    context = {
        "user_name": (getattr(user, "name", None) or "").strip()
                     or (getattr(user, "email", "") or "").split("@")[0]
                     or "there",
    }

    created: list[int] = []
    try:
        from app import db
        if LifecycleEmail is None:
            register_lifecycle_drip_model()
        if LifecycleEmail is None:
            log.error("enroll: LifecycleEmail model unavailable")
            return []

        for email_key in EMAIL_KEYS:
            sched = _schedule_for(email_key, event, now=now)
            if sched is None:
                continue

            # Idempotency: skip if a row already exists for this user+key+cohort.
            existing = (
                LifecycleEmail.query
                .filter_by(
                    user_id=user.id,
                    email_key=email_key,
                    cohort_id=cohort,
                )
                .first()
            )
            if existing is not None:
                created.append(existing.id)
                continue

            row = LifecycleEmail(
                user_id=user.id,
                email_key=email_key,
                cohort_id=cohort,
                scheduled_at=sched,
                send_status=STATUS_PENDING,
                context_json=json.dumps(context),
            )
            db.session.add(row)
            db.session.flush()
            created.append(row.id)

        db.session.commit()
    except Exception as exc:
        log.exception("enroll: DB write failed for user=%s event=%s: %s",
                      getattr(user, "id", "?"), event, exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return []

    log.info("enroll: user=%s event=%s -> scheduled %d email(s)",
             user.id, event, len(created))
    return created


# --------------------------------------------------------------------------- #
# Composition.
# --------------------------------------------------------------------------- #

_SUBJECTS = {
    "welcome": "Welcome to FIESTA — your tax savings start now",
    "calculator_nudge": "See what you'd save — try the FIESTA calculator",
    "payment_thanks": "You're in. Here's how to upload your evidence.",
    "sep30_30day": "Heads up: your tax payment is due in 30 days",
    "nov30_30day": "30 days to file: get your A&L into FIESTA",
}


def compose(email_key: str, user, context: Optional[dict] = None) -> dict:
    """Render the subject + HTML for one drip email.

    Args:
        email_key: must be in EMAIL_KEYS.
        user:      User ORM row (.email, .name).
        context:   extra merge fields (overlaid onto frozen context_json).

    Returns:
        {"to": str, "subject": str, "html": str}
    """
    if email_key not in EMAIL_KEYS:
        raise ValueError(f"unknown email_key {email_key!r}")

    name = (
        (context or {}).get("user_name")
        or (getattr(user, "name", None) or "").strip()
        or (getattr(user, "email", "") or "").split("@")[0]
        or "there"
    )
    email = getattr(user, "email", "") or ""
    subject = _SUBJECTS[email_key]

    template_name = f"emails/lifecycle/{email_key}.html"
    try:
        html = render_template(
            template_name,
            user_name=name,
            user_email=email,
            subject=subject,
        )
    except Exception as exc:
        # Tests / worker without app_context: degrade to a tiny inline body
        # so send() can still log + record. We never block the drip on a
        # template error.
        log.warning(
            "compose: render_template failed for %s (%s) — using fallback",
            template_name, exc,
        )
        html = (
            f"<html><body><h1>{subject}</h1>"
            f"<p>Hi {name},</p>"
            f"<p>(Template render failed; inline fallback.)</p>"
            f"</body></html>"
        )

    return {"to": email, "subject": subject, "html": html}


# --------------------------------------------------------------------------- #
# Send (STUBBED — see TODO).
# --------------------------------------------------------------------------- #

def _send_stub(payload: dict) -> tuple[bool, Optional[str]]:
    """Stub for actual email delivery.

    Logs the payload + fires a low-severity ops alert so visibility exists
    while SES/Mailgun is being wired. Returns (success, failure_reason).
    Always returns success=True in dev mode; provider integration will
    swap this body for an HTTP call.
    """
    try:
        log.info(
            "[lifecycle_drip STUB SEND] to=%s subject=%r html_len=%d",
            payload.get("to"), payload.get("subject"),
            len(payload.get("html") or ""),
        )
        try:
            from ops_alerts import send_alert
            send_alert(
                severity="LOW",
                title="Lifecycle drip (stub send)",
                body=(
                    f"to={payload.get('to')}, "
                    f"subject={payload.get('subject')!r}"
                ),
                data={
                    "to": payload.get("to"),
                    "subject": payload.get("subject"),
                    "html_length": len(payload.get("html") or ""),
                    "stubbed": True,
                },
            )
        except Exception as exc:
            log.debug("_send_stub: ops_alert non-fatal failure: %s", exc)
        return True, None
    except Exception as exc:
        log.exception("_send_stub failed: %s", exc)
        return False, str(exc)[:512]


def send(lifecycle_email) -> bool:
    """Send one queued drip email. Records sent_at / send_status / failure.

    Skip-conditions (recorded as 'skipped' with reason):
      * calculator_nudge for a user who has already run the estimator
      * row not in 'pending' state
      * user missing or no email
    """
    if lifecycle_email is None:
        return False
    if lifecycle_email.send_status != STATUS_PENDING:
        log.debug("send: row %s not pending (status=%s) — no-op",
                  lifecycle_email.id, lifecycle_email.send_status)
        return False

    try:
        from app import db
        from models import User

        user = User.query.get(lifecycle_email.user_id)
        if user is None or not (getattr(user, "email", "") or "").strip():
            lifecycle_email.send_status = STATUS_SKIPPED
            lifecycle_email.failure_reason = "user missing or no email"
            lifecycle_email.sent_at = datetime.utcnow()
            db.session.commit()
            return False

        # Behavioural skip: don't nag a user who has already calculated.
        if lifecycle_email.email_key == "calculator_nudge":
            if _user_has_calculated(user.id):
                lifecycle_email.send_status = STATUS_SKIPPED
                lifecycle_email.failure_reason = (
                    "user already ran calculator before nudge fire"
                )
                lifecycle_email.sent_at = datetime.utcnow()
                db.session.commit()
                log.info("send: skipped calculator_nudge for user=%s "
                         "(already calculated)", user.id)
                return False

        context = {}
        try:
            if lifecycle_email.context_json:
                context = json.loads(lifecycle_email.context_json) or {}
        except Exception:
            context = {}

        payload = compose(lifecycle_email.email_key, user, context)
        ok, reason = _send_stub(payload)

        lifecycle_email.sent_at = datetime.utcnow()
        if ok:
            lifecycle_email.send_status = STATUS_SENT
        else:
            lifecycle_email.send_status = STATUS_FAILED
            lifecycle_email.failure_reason = reason
        db.session.commit()
        return ok
    except Exception as exc:
        log.exception("send: failed for row=%s: %s",
                      getattr(lifecycle_email, "id", "?"), exc)
        try:
            from app import db
            db.session.rollback()
            lifecycle_email.send_status = STATUS_FAILED
            lifecycle_email.failure_reason = str(exc)[:512]
            lifecycle_email.sent_at = datetime.utcnow()
            db.session.commit()
        except Exception:
            pass
        return False


def scan_and_send(limit: int = 200) -> dict:
    """Find pending rows with scheduled_at<=now and send each.

    Returns a small dict with counts: {"scanned": N, "sent": N,
    "skipped": N, "failed": N}.
    """
    counts = {"scanned": 0, "sent": 0, "skipped": 0, "failed": 0}
    try:
        from app import db
        if LifecycleEmail is None:
            register_lifecycle_drip_model()
        if LifecycleEmail is None:
            return counts

        now = datetime.utcnow()
        rows = (
            LifecycleEmail.query
            .filter(
                LifecycleEmail.send_status == STATUS_PENDING,
                LifecycleEmail.scheduled_at <= now,
            )
            .order_by(LifecycleEmail.scheduled_at.asc())
            .limit(limit)
            .all()
        )
        counts["scanned"] = len(rows)
        for r in rows:
            ok = send(r)
            if r.send_status == STATUS_SENT:
                counts["sent"] += 1
            elif r.send_status == STATUS_SKIPPED:
                counts["skipped"] += 1
            elif r.send_status == STATUS_FAILED:
                counts["failed"] += 1
    except Exception as exc:
        log.exception("scan_and_send: failed: %s", exc)

    return counts


# --------------------------------------------------------------------------- #
# TODO — wire actual email delivery (SES vs Mailgun).
# --------------------------------------------------------------------------- #
#
# FIESTA currently uses SendGrid for transactional sends (sendgrid_logger.py
# + email_verification.py). When the drip ships to production:
#
#   Recommendation: ride the existing SendGrid integration FIRST (lowest
#   marginal cost — keys + reputation already established). Migrate to AWS
#   SES only if monthly volume crosses ~50k mails (SES is ~10x cheaper at
#   scale but adds IAM + bounce-handling work). Mailgun is the safety net
#   if SendGrid blocks LK-sender reputation.
#
#   Implementation path:
#     1. Replace _send_stub body with a call to sendgrid_logger.send(...)
#        already in this repo.
#     2. Add a list-unsubscribe header (CAN-SPAM compliance — currently
#        OUT OF SCOPE because the stub doesn't ship to real inboxes).
#     3. Add a `bounced_at` column + Stripe-style webhook to flip
#        send_status='bounced' when SendGrid reports a hard bounce; pause
#        further drips for that user.
#     4. Promote ops_alerts severity from LOW -> MEDIUM only for
#        deliverability failures, not every send.
#
# Council cap stays binding: 5 emails total. Do not extend EMAIL_KEYS
# without a fresh council pass.
