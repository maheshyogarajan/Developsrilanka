"""fiesta.cosign.models -- DB models for the S10 co-sign workflow.

Wave 3 (2026-05-20). Per S10 dispatch brief.

Two tables
----------
CosignWorkflow   -- one row per "customer-prepared-and-wants-SP-to-sign" loop.
                    FK to service_agreements (S8). Status moves linearly
                    through the lifecycle. Captures: SP email, signing
                    method, signature artefacts (typed name / IP / UA),
                    timestamps for every state transition, and a single-
                    use tracking_token the SP uses to access the signing
                    page without auth.

CosignReminder   -- one row per reminder fired. The scheduler queries this
                    table to decide whether the cadence is satisfied. Status
                    captures whether the SendGrid call succeeded so a retry
                    doesn't double-send.

Status flow
-----------
    drafted                     -- workflow created, customer hasn't sent
    sent_to_sp                  -- email dispatched to SP
    sp_viewed                   -- SP clicked the tracking link
    sp_signed                   -- SP completed signature
    customer_countersigned      -- customer signed after SP
    complete                    -- both parties signed
    abandoned                   -- customer marked "I handled this offline"

Linear with one branch: sent_to_sp -> sp_viewed -> sp_signed (typed-name OR
offline-scan upload). Reach customer_countersigned -> complete. Customer
can abandon from any pre-complete state.

Privacy + retention
-------------------
SP signature artefacts retained 7y for IRD audit defence (matches S8
template-version retention). The data lives only on this table; never
exposed cross-customer.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Iterable

from app import db


# ---------------------------------------------------------------------------
# Status / method constants -- kept as module-level so the routes + tests
# can import them without instantiating a model.
# ---------------------------------------------------------------------------
COSIGN_STATUS_DRAFTED = "drafted"
COSIGN_STATUS_SENT_TO_SP = "sent_to_sp"
COSIGN_STATUS_SP_VIEWED = "sp_viewed"
COSIGN_STATUS_SP_SIGNED = "sp_signed"
COSIGN_STATUS_CUSTOMER_COUNTERSIGNED = "customer_countersigned"
COSIGN_STATUS_COMPLETE = "complete"
COSIGN_STATUS_ABANDONED = "abandoned"

ALL_COSIGN_STATUSES = (
    COSIGN_STATUS_DRAFTED,
    COSIGN_STATUS_SENT_TO_SP,
    COSIGN_STATUS_SP_VIEWED,
    COSIGN_STATUS_SP_SIGNED,
    COSIGN_STATUS_CUSTOMER_COUNTERSIGNED,
    COSIGN_STATUS_COMPLETE,
    COSIGN_STATUS_ABANDONED,
)

# Open / in-progress states the daily scheduler iterates over.
IN_PROGRESS_STATUSES = (
    COSIGN_STATUS_SENT_TO_SP,
    COSIGN_STATUS_SP_VIEWED,
    COSIGN_STATUS_SP_SIGNED,
)

# SP signing methods captured for the audit trail.
SIGNING_METHOD_TYPED_NAME = "typed-name"
SIGNING_METHOD_PRINTED_PDF = "printed-pdf"
SIGNING_METHOD_DOCUSIGN = "DocuSign"
SIGNING_METHOD_EXTERNAL_PRINT = "external-print"


# Token lifetime + reminder cadence
TRACKING_TOKEN_TTL_DAYS = 30
REMINDER_FIRST_OFFSET_DAYS = 3
REMINDER_SECOND_OFFSET_DAYS = 7
REMINDER_ESCALATE_OFFSET_DAYS = 14


def _generate_tracking_token() -> str:
    """Cryptographically-random URL-safe token. 32 bytes -> ~43 chars
    base64url. Single-use -- once an SP signs, the workflow's status
    flips and route handlers refuse the same token afterwards.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# CosignWorkflow
# ---------------------------------------------------------------------------


class CosignWorkflow(db.Model):
    """One row per customer-initiated co-sign loop. FK to service_agreements."""

    __tablename__ = "cosign_workflows"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )
    service_agreement_id = db.Column(
        db.Integer,
        db.ForeignKey("service_agreements.id"),
        nullable=False,
        index=True,
    )

    # Status -- the lifecycle.
    status = db.Column(
        db.String(40),
        nullable=False,
        default=COSIGN_STATUS_DRAFTED,
        index=True,
    )

    # Tracking token + lifecycle.
    tracking_token = db.Column(
        db.String(80),
        nullable=False,
        unique=True,
        index=True,
        default=_generate_tracking_token,
    )
    tracking_token_expires_at = db.Column(db.DateTime, nullable=True)

    # Counterparty + outreach.
    sp_email = db.Column(db.String(255), nullable=True)
    sp_name = db.Column(db.String(255), nullable=True)
    sp_signing_method = db.Column(db.String(40), nullable=True)

    # State-transition timestamps.
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer_email_sent_at = db.Column(db.DateTime, nullable=True)
    sp_email_clicked_at = db.Column(db.DateTime, nullable=True)
    sp_signed_at = db.Column(db.DateTime, nullable=True)
    customer_countersigned_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    abandoned_at = db.Column(db.DateTime, nullable=True)

    # SP signing artefacts (Electronic Transactions Act No. 19 of 2006 compliant).
    sp_typed_name = db.Column(db.String(255), nullable=True)
    sp_signature_ip = db.Column(db.String(64), nullable=True)
    sp_signature_ua = db.Column(db.String(255), nullable=True)
    sp_offline_scan_path = db.Column(db.String(512), nullable=True)

    # Concerns / declines / handoff to support@.
    sp_declined_at = db.Column(db.DateTime, nullable=True)
    sp_decline_message = db.Column(db.Text, nullable=True)

    # Customer countersign artefact.
    customer_typed_name = db.Column(db.String(255), nullable=True)
    customer_signature_ip = db.Column(db.String(64), nullable=True)

    # Bookkeeping: last reminder so the scheduler knows when next to fire.
    last_reminder_at = db.Column(db.DateTime, nullable=True)
    reminder_count = db.Column(db.Integer, nullable=False, default=0)

    # Escalation -- T+14d unresolved. Once true we don't keep alerting.
    ceo_escalated_at = db.Column(db.DateTime, nullable=True)

    def __init__(self, **kwargs):
        # Set token expiry if not supplied.
        if "tracking_token_expires_at" not in kwargs:
            kwargs["tracking_token_expires_at"] = datetime.utcnow() + timedelta(
                days=TRACKING_TOKEN_TTL_DAYS
            )
        super().__init__(**kwargs)

    @property
    def is_token_expired(self) -> bool:
        if self.tracking_token_expires_at is None:
            return False
        return datetime.utcnow() > self.tracking_token_expires_at

    @property
    def is_terminal(self) -> bool:
        """True when no further state transitions are valid."""
        return self.status in (COSIGN_STATUS_COMPLETE, COSIGN_STATUS_ABANDONED)

    @property
    def is_in_progress(self) -> bool:
        return self.status in IN_PROGRESS_STATUSES

    def __repr__(self) -> str:  # pragma: no cover -- debug only
        return (
            f"<CosignWorkflow id={self.id} status={self.status} "
            f"user_id={self.user_id} agreement_id={self.service_agreement_id}>"
        )


# ---------------------------------------------------------------------------
# CosignReminder
# ---------------------------------------------------------------------------


class CosignReminder(db.Model):
    """One row per reminder firing. Lets the scheduler enforce cadence."""

    __tablename__ = "cosign_reminders"

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(
        db.Integer,
        db.ForeignKey("cosign_workflows.id"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.String(40), nullable=False)
    #   "first_3d"   -- gentle nudge at T+3d
    #   "second_7d"  -- second nudge at T+7d
    #   "escalate_14d" -- CEO/staff notification + last nudge

    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sendgrid_status = db.Column(db.String(40), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover -- debug only
        return f"<CosignReminder workflow_id={self.workflow_id} kind={self.kind}>"


# ---------------------------------------------------------------------------
# Helpers shared with routes + scheduler
# ---------------------------------------------------------------------------


def reminders_due(workflow: CosignWorkflow, now: datetime | None = None) -> Iterable[str]:
    """Yield reminder kinds (first_3d / second_7d / escalate_14d) that are
    due for `workflow` given `now`. Uses customer_email_sent_at as the
    anchor; if it's never been sent we yield nothing.

    Idempotency is enforced upstream: the scheduler queries CosignReminder
    rows so we never fire the same kind twice on the same workflow.
    """
    now = now or datetime.utcnow()

    if workflow.is_terminal:
        return
    if workflow.status == COSIGN_STATUS_DRAFTED:
        return
    if workflow.customer_email_sent_at is None:
        return

    elapsed = now - workflow.customer_email_sent_at
    if elapsed >= timedelta(days=REMINDER_ESCALATE_OFFSET_DAYS):
        yield "escalate_14d"
        return  # escalate_14d supersedes earlier kinds
    if elapsed >= timedelta(days=REMINDER_SECOND_OFFSET_DAYS):
        yield "second_7d"
        return
    if elapsed >= timedelta(days=REMINDER_FIRST_OFFSET_DAYS):
        yield "first_3d"


__all__ = [
    "CosignWorkflow",
    "CosignReminder",
    "COSIGN_STATUS_DRAFTED",
    "COSIGN_STATUS_SENT_TO_SP",
    "COSIGN_STATUS_SP_VIEWED",
    "COSIGN_STATUS_SP_SIGNED",
    "COSIGN_STATUS_CUSTOMER_COUNTERSIGNED",
    "COSIGN_STATUS_COMPLETE",
    "COSIGN_STATUS_ABANDONED",
    "ALL_COSIGN_STATUSES",
    "IN_PROGRESS_STATUSES",
    "SIGNING_METHOD_TYPED_NAME",
    "SIGNING_METHOD_PRINTED_PDF",
    "SIGNING_METHOD_DOCUSIGN",
    "SIGNING_METHOD_EXTERNAL_PRINT",
    "TRACKING_TOKEN_TTL_DAYS",
    "REMINDER_FIRST_OFFSET_DAYS",
    "REMINDER_SECOND_OFFSET_DAYS",
    "REMINDER_ESCALATE_OFFSET_DAYS",
    "reminders_due",
]
