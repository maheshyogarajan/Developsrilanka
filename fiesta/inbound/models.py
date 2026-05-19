"""fiesta.inbound.models — SQLAlchemy + pydantic models for X5.

Two database tables:
    - InboundEmail: every inbound webhook hit (parsed + classified + linked
      to a User when matchable).
    - OutboundDraft: the Tier-1-gated draft auto-reply queued for staff
      approval. NEVER auto-sent.

Privacy:
    - When customer_matched=False, we still persist the InboundEmail row but
      strip the body to a fixed-length excerpt (PRIVACY_UNMATCHED_BODY_CHARS).
      The full body is dropped. Subject + from_address are retained for
      staff triage.
    - When customer_matched=True, the full body is retained.

The db import is lazy so this module can be imported (for read-only schema
inspection / pydantic-only consumers) in environments without Flask installed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# Privacy knobs
PRIVACY_UNMATCHED_BODY_CHARS = 200
INBOUND_BODY_MAX_CHARS = 60_000  # ~60 KB; reject larger bodies upstream
SUBJECT_MAX_CHARS = 1000
FROM_ADDR_MAX_CHARS = 320  # RFC 5321 max


# ---------------------------------------------------------------------------
# pydantic v2 DTOs (used by webhook + tests; mirror the SQLAlchemy schemas)
# ---------------------------------------------------------------------------

class InboundEmailDTO(BaseModel):
    """Pydantic v2 mirror of InboundEmail for testing without the DB."""

    id: Optional[int] = None
    from_addr: str = Field(..., max_length=FROM_ADDR_MAX_CHARS)
    to_addr: Optional[str] = Field(default=None, max_length=FROM_ADDR_MAX_CHARS)
    subject: Optional[str] = Field(default=None, max_length=SUBJECT_MAX_CHARS)
    body_text: Optional[str] = Field(default=None, max_length=INBOUND_BODY_MAX_CHARS)
    body_html: Optional[str] = Field(default=None, max_length=INBOUND_BODY_MAX_CHARS)
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    received_at: datetime = Field(default_factory=datetime.utcnow)
    customer_id: Optional[int] = None
    customer_matched: bool = False
    classified_as: Optional[str] = None
    classifier_score: Optional[int] = None
    classifier_reasoning: Optional[str] = None
    is_autoreply_noise: bool = False
    status: str = Field(default="pending")
    # Status lifecycle:
    #   pending -> classified -> drafted -> approved -> sent
    #   pending -> classified -> drafted -> dismissed
    #   pending -> flagged_for_staff (unmatched / noise / errors)
    notes: Optional[str] = None


class OutboundDraftDTO(BaseModel):
    """Pydantic v2 mirror of OutboundDraft."""

    id: Optional[int] = None
    inbound_email_id: int
    draft_subject: str = Field(..., max_length=SUBJECT_MAX_CHARS)
    draft_body: str
    linkback_url: Optional[str] = None
    category: str
    route_hint: str
    ready_for_approval_at: datetime = Field(default_factory=datetime.utcnow)
    approved_by: Optional[int] = None
    approved_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    dismissed_reason: Optional[str] = None
    edit_history: Optional[str] = None  # JSON-encoded diff log
    gates_failed: Optional[str] = None  # JSON-encoded list
    warnings: Optional[str] = None      # JSON-encoded list

    @property
    def status(self) -> str:
        if self.sent_at:
            return "sent"
        if self.dismissed_at:
            return "dismissed"
        if self.approved_at:
            return "approved"
        return "ready_for_approval"


# ---------------------------------------------------------------------------
# SQLAlchemy models (lazy-bound via the same `db` as the rest of FIESTA)
# ---------------------------------------------------------------------------

def _redact_body_for_unmatched(body: Optional[str]) -> Optional[str]:
    """If we couldn't match the inbound to a customer, only keep a short
    excerpt — privacy guard for arbitrary inbound mail."""
    if not body:
        return body
    if len(body) <= PRIVACY_UNMATCHED_BODY_CHARS:
        return body
    return body[:PRIVACY_UNMATCHED_BODY_CHARS] + "...[truncated for privacy]"


def build_sqlalchemy_models(db: Any) -> dict[str, Any]:
    """Build InboundEmail + OutboundDraft on the provided db instance.

    Called from app.py (or the same place models.py imports `db`) at module
    init time. The function pattern avoids hard-importing Flask at module
    load — keeps the classifier + router testable without a DB.

    Returns:
        dict with keys 'InboundEmail', 'OutboundDraft'.
    """

    class InboundEmail(db.Model):
        __tablename__ = "inbound_emails"

        id = db.Column(db.Integer, primary_key=True)
        from_addr = db.Column(db.String(FROM_ADDR_MAX_CHARS), nullable=False, index=True)
        to_addr = db.Column(db.String(FROM_ADDR_MAX_CHARS), nullable=True)
        subject = db.Column(db.String(SUBJECT_MAX_CHARS), nullable=True)
        body_text = db.Column(db.Text, nullable=True)
        body_html = db.Column(db.Text, nullable=True)

        # Threading
        message_id = db.Column(db.String(512), nullable=True, index=True)
        in_reply_to = db.Column(db.String(512), nullable=True, index=True)
        references = db.Column(db.Text, nullable=True)

        received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

        # Matching
        customer_id = db.Column(db.Integer, nullable=True, index=True)
        customer_matched = db.Column(db.Boolean, default=False, nullable=False)

        # Classification
        classified_as = db.Column(db.String(64), nullable=True, index=True)
        classifier_score = db.Column(db.Integer, nullable=True)
        classifier_reasoning = db.Column(db.Text, nullable=True)
        is_autoreply_noise = db.Column(db.Boolean, default=False, nullable=False)

        # Lifecycle
        status = db.Column(db.String(32), default="pending", nullable=False, index=True)
        notes = db.Column(db.Text, nullable=True)

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "from_addr": self.from_addr,
                "to_addr": self.to_addr,
                "subject": self.subject,
                "body_text": self.body_text,
                "body_html": self.body_html,
                "message_id": self.message_id,
                "in_reply_to": self.in_reply_to,
                "references": self.references,
                "received_at": self.received_at.isoformat() if self.received_at else None,
                "customer_id": self.customer_id,
                "customer_matched": self.customer_matched,
                "classified_as": self.classified_as,
                "classifier_score": self.classifier_score,
                "classifier_reasoning": self.classifier_reasoning,
                "is_autoreply_noise": self.is_autoreply_noise,
                "status": self.status,
                "notes": self.notes,
            }

    class OutboundDraft(db.Model):
        __tablename__ = "outbound_drafts"

        id = db.Column(db.Integer, primary_key=True)
        inbound_email_id = db.Column(
            db.Integer,
            db.ForeignKey("inbound_emails.id"),
            nullable=False,
            index=True,
        )

        draft_subject = db.Column(db.String(SUBJECT_MAX_CHARS), nullable=False)
        draft_body = db.Column(db.Text, nullable=False)
        linkback_url = db.Column(db.String(1024), nullable=True)
        category = db.Column(db.String(64), nullable=False, index=True)
        route_hint = db.Column(db.String(64), nullable=False)

        ready_for_approval_at = db.Column(
            db.DateTime, default=datetime.utcnow, nullable=False, index=True,
        )
        approved_by = db.Column(db.Integer, nullable=True)
        approved_at = db.Column(db.DateTime, nullable=True)
        sent_at = db.Column(db.DateTime, nullable=True)
        dismissed_at = db.Column(db.DateTime, nullable=True)
        dismissed_reason = db.Column(db.String(256), nullable=True)

        edit_history = db.Column(db.Text, nullable=True)  # JSON
        gates_failed = db.Column(db.Text, nullable=True)  # JSON
        warnings = db.Column(db.Text, nullable=True)      # JSON

        @property
        def status(self) -> str:
            if self.sent_at:
                return "sent"
            if self.dismissed_at:
                return "dismissed"
            if self.approved_at:
                return "approved"
            return "ready_for_approval"

        def to_dict(self) -> dict[str, Any]:
            return {
                "id": self.id,
                "inbound_email_id": self.inbound_email_id,
                "draft_subject": self.draft_subject,
                "draft_body": self.draft_body,
                "linkback_url": self.linkback_url,
                "category": self.category,
                "route_hint": self.route_hint,
                "ready_for_approval_at": (
                    self.ready_for_approval_at.isoformat()
                    if self.ready_for_approval_at else None
                ),
                "approved_by": self.approved_by,
                "approved_at": self.approved_at.isoformat() if self.approved_at else None,
                "sent_at": self.sent_at.isoformat() if self.sent_at else None,
                "dismissed_at": self.dismissed_at.isoformat() if self.dismissed_at else None,
                "dismissed_reason": self.dismissed_reason,
                "status": self.status,
            }

    return {"InboundEmail": InboundEmail, "OutboundDraft": OutboundDraft}


__all__ = [
    "InboundEmailDTO",
    "OutboundDraftDTO",
    "build_sqlalchemy_models",
    "_redact_body_for_unmatched",
    "PRIVACY_UNMATCHED_BODY_CHARS",
    "INBOUND_BODY_MAX_CHARS",
    "SUBJECT_MAX_CHARS",
    "FROM_ADDR_MAX_CHARS",
]
