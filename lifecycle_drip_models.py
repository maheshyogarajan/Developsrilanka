"""
lifecycle_drip_models.py — Tier D4 / A5: Lifecycle email drip ORM.

Defines the LifecycleEmail row: one scheduled-or-sent email in the drip
sequence. Late-bound model registration mirrors the dunning_sequence.py
pattern so the worker process imports cleanly without a full Flask app.

Schema
------
  id              SERIAL PK
  user_id         FK -> "user".id
  email_key       string ("welcome", "calculator_nudge", "payment_thanks",
                  "sep30_30day", "nov30_30day") — picked from EMAIL_KEYS.
  cohort_id       string yyyy-mm bucket (e.g. "2026-05"). Lets the same
                  user receive the same deadline reminder in a NEW tax
                  year without dedup collision while still blocking
                  duplicate sends within the same cycle.
  scheduled_at    UTC timestamp the Celery beat should send at-or-after.
  sent_at         UTC timestamp of actual send. NULL = pending.
  send_status     "pending" | "sent" | "failed" | "skipped"
  failure_reason  short text (NULL unless send_status='failed' or 'skipped')
  context_json    JSON blob with merge fields the template consumed at
                  enroll time (e.g. user_name) — frozen at enrolment so a
                  later name change can't rewrite history mid-flight.
  created_at      UTC timestamp enrol fired.

Uniqueness:
  (user_id, email_key, cohort_id) — enforces one enrolment per cohort.

Council cap: only 5 valid email_keys ever. EMAIL_KEYS is the gate.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)


# Authoritative cap of 5 email keys (Council).
EMAIL_KEYS = (
    "welcome",            # day 0 post-signup
    "calculator_nudge",   # day 1 if no estimator_run event
    "payment_thanks",     # immediate post-payment
    "sep30_30day",        # 30d before Sep 30 tax payment
    "nov30_30day",        # 30d before Nov 30 tax return
)

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


LifecycleEmail = None  # type: ignore[assignment]
_registered = False


def register_lifecycle_drip_model():
    """Define LifecycleEmail ORM against the live ``db``. Idempotent."""
    global LifecycleEmail, _registered
    if _registered:
        return LifecycleEmail

    from app import db

    class _LifecycleEmail(db.Model):  # type: ignore[misc, valid-type]
        __tablename__ = "lifecycle_email"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(
            db.Integer, db.ForeignKey("user.id"),
            nullable=False, index=True,
        )
        email_key = db.Column(db.String(64), nullable=False, index=True)
        cohort_id = db.Column(db.String(16), nullable=False, index=True)
        scheduled_at = db.Column(
            db.DateTime, nullable=False, index=True,
        )
        sent_at = db.Column(db.DateTime, nullable=True)
        send_status = db.Column(
            db.String(16), nullable=False, default=STATUS_PENDING, index=True,
        )
        failure_reason = db.Column(db.String(512), nullable=True)
        context_json = db.Column(db.Text, nullable=True)
        created_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow,
        )

        __table_args__ = (
            db.UniqueConstraint(
                "user_id", "email_key", "cohort_id",
                name="uq_lifecycle_email_user_key_cohort",
            ),
        )

        def __repr__(self):  # pragma: no cover
            return (
                f"<LifecycleEmail id={self.id} user={self.user_id} "
                f"key={self.email_key} cohort={self.cohort_id} "
                f"status={self.send_status}>"
            )

    LifecycleEmail = _LifecycleEmail
    _registered = True
    log.info(
        "lifecycle_drip: LifecycleEmail model registered (lifecycle_email)"
    )
    return LifecycleEmail
