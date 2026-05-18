"""
Support Copilot models — Wave 3.2 (2026-05-18).

Council #2 constraint (the tax-adjacent-hallucination guard): every Copilot
answer cites deterministic source nodes (a user's ledger row, an IRD circular
section, a specific past audit_log entry). No free-text rule interpretation;
low-confidence answers escalate to a human, never bluff.

This module owns the persistence side of that contract:

  * `support_tickets` — one row per user question. Tracks the question, the
    AI-drafted answer with citations + confidence, escalation state, and the
    eventual human answer (when escalated) + CSAT (when resolved).

Schema-additive pattern (mirrors event_models.py + gemini_cost_log_model.py +
ai_crm.CustomerProfile):
  (a) raw CREATE TABLE IF NOT EXISTS in _ensure_support_tickets_table() runs
      at module import so the table is present whenever any caller imports
      SupportTicket. Belt-and-braces against delayed metadata reflection
      (Celery boot order, etc.).
  (b) db.create_all() in main.py picks up this model via SQLAlchemy metadata
      when the module is imported.

The ORM model is what application code uses for queries; the raw DDL
guarantees the table is present even if SQLAlchemy metadata reflection is
delayed (gunicorn vs celery boot order, etc).
"""
from __future__ import annotations

import logging
from datetime import datetime

from app import db

log = logging.getLogger(__name__)


class SupportTicket(db.Model):
    """One user question to the AI Support Copilot.

    Lifecycle:
      1. Created with question + ai_answer + citations + confidence (auto path)
         OR question only + escalated_to_human=True + escalation_reason
         (escalated path).
      2. (Escalated only) human reviews via /admin/support/queue, posts
         human_answer + resolved_at via the resolve route.
      3. (Optional) user submits csat_rating 1-5 via the CSAT route.

    Council #2 constraint enforced at the application layer (support_copilot.py):
      * `citations` is the deterministic-source-node list (KB ids, ledger row
        refs, audit_log row refs). If empty AND not escalated, the record is
        invalid — the copilot must escalate instead.
      * `confidence` < 0.7 → must escalate (escalated_to_human=True).
    """
    __tablename__ = "support_tickets"

    id = db.Column(db.Integer, primary_key=True)

    # ON DELETE CASCADE — when a user is purged (GDPR / test cleanup), their
    # support history goes with them. Same logic as ai_crm.CustomerProfile.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The user's free-text question. NOT NULL — every ticket must have one.
    question = db.Column(db.Text, nullable=False)

    # The AI-drafted answer. Nullable: an escalated-before-Gemini ticket
    # (escalation_keyword hit in question text) carries no AI answer.
    ai_answer = db.Column(db.Text, nullable=True)

    # List of citation refs the AI used. Shape (council #2 contract):
    #   [{"kind": "kb",       "id": "pn_it_2025_01_overview"},
    #    {"kind": "ledger",   "id": 12345, "summary": "USD 1,200 on 2026-04-15"},
    #    {"kind": "audit",    "id": 67890, "summary": "INSERT remittance_entry"}]
    # If empty AND escalated_to_human=False → invalid (copilot bug; must escalate).
    citations = db.Column(db.JSON, nullable=True)

    # 0.00 to 1.00. Council #2: < 0.7 → mandatory escalation.
    # NUMERIC(3,2) supports values 0.00 through 9.99; we clamp to [0, 1] at
    # write time in support_copilot.answer_question().
    confidence = db.Column(db.Numeric(3, 2), nullable=True)

    # True when the ticket is queued for human review (low confidence,
    # red-flag keyword, missing citations, or Gemini failure).
    escalated_to_human = db.Column(
        db.Boolean, nullable=False, default=False, index=True,
    )

    # Why was it escalated? Free-form short slug for the admin queue UI.
    # Examples: 'low_confidence', 'keyword:audit', 'no_citations', 'gemini_error'.
    escalation_reason = db.Column(db.String(128), nullable=True)

    # Human reviewer's answer, posted via /admin/support/<id>/resolve.
    # Nullable until the human resolves the ticket.
    human_answer = db.Column(db.Text, nullable=True)

    # Wall-clock when the ticket was resolved. For auto-answered tickets this
    # is set at the same time as created_at (the AI answer IS the resolution).
    # For escalated tickets, set when a human posts human_answer.
    resolved_at = db.Column(db.DateTime, nullable=True)

    # 1-5 Likert rating submitted by the user after they read the answer.
    # Nullable — most users won't bother. NEVER prompt twice.
    csat_rating = db.Column(db.SmallInteger, nullable=True)

    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True,
    )

    # Composite indexes — designed for the two access patterns the routes
    # exercise:
    #   "per-user ticket history, newest first"  -> (user_id, created_at DESC)
    #   "admin queue of open escalations"        -> partial index on
    #                                                escalated_to_human=true
    #                                                AND resolved_at IS NULL
    __table_args__ = (
        db.Index(
            "ix_support_tickets_user_created_at",
            "user_id",
            db.text("created_at DESC"),
        ),
        db.Index(
            "ix_support_tickets_open_escalations",
            "escalated_to_human",
            "resolved_at",
        ),
    )

    def __repr__(self):
        return (
            f"<SupportTicket {self.id} user={self.user_id} "
            f"escalated={self.escalated_to_human} confidence={self.confidence}>"
        )


def _ensure_support_tickets_table():
    """Idempotent. Runs on import; cheap.

    Belt-and-braces: SQLAlchemy's db.create_all() picks up this model via the
    metadata registry when the module is imported, BUT in environments where
    metadata reflection is delayed (e.g. Celery worker boot order), the raw
    DDL below guarantees the table exists by the time the first route hits it.

    Mirrors the pattern in fx_rate_service._ensure_fx_table(),
    gemini_cost_log_model._ensure_gemini_cost_log_table(), and
    ai_crm._ensure_customer_profiles_table().
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text("""
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL
                        REFERENCES "user"(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    ai_answer TEXT NULL,
                    citations JSON NULL,
                    confidence NUMERIC(3, 2) NULL,
                    escalated_to_human BOOLEAN NOT NULL DEFAULT FALSE,
                    escalation_reason VARCHAR(128) NULL,
                    human_answer TEXT NULL,
                    resolved_at TIMESTAMP NULL,
                    csat_rating SMALLINT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_support_tickets_user_id
                    ON support_tickets (user_id)
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_support_tickets_user_created_at
                    ON support_tickets (user_id, created_at DESC)
            """))
            # Partial index for the admin queue — only open escalations.
            # Postgres-only but the production DB is Neon (Postgres). The
            # IF NOT EXISTS makes re-runs cheap.
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_support_tickets_open_escalations
                    ON support_tickets (escalated_to_human, resolved_at)
                    WHERE escalated_to_human = TRUE AND resolved_at IS NULL
            """))
            db.session.execute(_sql_text("""
                CREATE INDEX IF NOT EXISTS ix_support_tickets_created_at
                    ON support_tickets (created_at)
            """))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure support_tickets table: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


# Run the idempotent DDL at module import. Cheap; runs once per process.
# Guarded inside the helper, so even a totally-broken DB at import time
# won't kill the module load.
_ensure_support_tickets_table()


__all__ = ["SupportTicket", "_ensure_support_tickets_table"]
