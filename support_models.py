"""
Support ticket models — Tier D2 (2026-05-24).

Lightweight Tier-2 escalation for FIESTA users when the in-app feedback widget
(D4) or the AI Support Copilot (Wave 3.2, support_copilot_models.py) can't
resolve the issue on its own. Customer files a ticket -> CEO sees it ->
CEO or a future AI replies -> customer sees the conversation in their portal.

Why a NEW pair of tables (not extending support_copilot_models.SupportTicket)?
  * `support_copilot_models.SupportTicket` is the single-shot Q&A model from
    the AI Support Copilot (question + ai_answer + citations + escalated_to_human
    + optional human_answer). It has NO conversation thread, NO priority, NO
    tags, NO assignee, NO category — adding those columns would mutate a live
    table that the Copilot's lifecycle code (support_copilot.py + admin queue +
    CSAT) reads. Disruptive and the wrong shape for Tier D2.
  * D2 is a conversation: customer can reply, staff can reply, AI can reply
    (future). That's a 1:many parent/child schema, not a flat row.
  * Keeping the tables separate means D2 can ship today without coordinating
    schema with Wave 3.2.

The bridge from D4 feedback to a D2 ticket lives in `feedback_routes.py`:
when feedback.category is 'bug' or 'confusion', a D2SupportTicket is auto-
created with the feedback body as the seed message. That is the only coupling.

Schema-additive pattern (mirrors event_models, feedback_models,
support_copilot_models):
  (a) Raw `CREATE TABLE IF NOT EXISTS ...` in `_ensure_d2_support_tables()`
      runs at module import so the tables exist whenever any caller imports.
      Belt-and-braces against delayed metadata reflection (gunicorn vs
      celery boot order).
  (b) `db.create_all()` in main.py picks the models up via SQLAlchemy metadata.
  (c) `migrations/add_support_tickets.py` provides an explicit, audit-logged
      upgrade path for ops who don't want to rely on import-time DDL.

CEO query for the open queue:
    SELECT * FROM d2_support_tickets
    WHERE status = 'open'
    ORDER BY
      CASE priority WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
      created_at DESC;
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import ARRAY

from app import db


log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Allowed values — kept in Python frozensets so the routes can validate
# without importing the DB schema, and mirrored as CHECK constraints in the
# raw DDL so a malformed direct INSERT can't pollute the queue.
# --------------------------------------------------------------------------- #
TICKET_STATUSES = frozenset({
    "open",                 # awaiting staff response
    "awaiting_customer",    # staff replied; ball in customer's court
    "resolved",             # answer accepted by customer (or staff-closed)
    "closed",               # archival; conversation locked
})

TICKET_PRIORITIES = frozenset({
    "low",
    "normal",
    "high",
})

# Mirrors feedback_models.FEEDBACK_CATEGORIES on purpose: when the D4 widget
# auto-bridges 'bug' / 'confusion' feedback into a ticket, the category
# carries across so triage can group both surfaces.
TICKET_CATEGORIES = frozenset({
    "bug",
    "feature",
    "confusion",
    "praise",
    "other",
})

MESSAGE_AUTHOR_ROLES = frozenset({
    "customer",     # the ticket owner
    "staff",        # any staff/admin user
    "ai",           # the AI copilot (future D1 integration)
    "system",       # auto-generated bridge note ("Filed from feedback widget")
})


class D2SupportTicket(db.Model):
    """One support ticket — a conversation between a customer and staff (or AI).

    Lifecycle:
      1. Created via POST /api/support/ticket (manual) or via the D4
         feedback-widget auto-bridge (when category is bug/confusion).
         Initial status='open', assignee defaults to CEO if known.
      2. Customer or staff posts D2SupportTicketMessage rows; replying moves
         status open<->awaiting_customer.
      3. Staff (or customer agreement) marks status='resolved' or 'closed'.

    The `id` is the public ticket number — keep it simple, no slug.
    """
    __tablename__ = "d2_support_tickets"

    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True,
    )
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Nullable: an anonymous user (no account yet) can still file a ticket
    # via the public widget if we wire it up later. ON DELETE SET NULL keeps
    # the row's aggregate value after an account purge.
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Mirrors the analytics-beacon cookie so anonymous tickets can be linked
    # back to the same browser's funnel events. Same shape as feedback.
    session_anon_id = db.Column(db.String(64), nullable=True, index=True)

    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)

    # Stored as short VARCHAR rather than native enums so adding a new value
    # later is a code change plus a CHECK-constraint update, not an enum migration.
    status = db.Column(
        db.String(32), nullable=False, default="open", index=True,
    )
    priority = db.Column(
        db.String(16), nullable=False, default="normal", index=True,
    )
    category = db.Column(db.String(32), nullable=True)

    # Assignee — the staff/admin user who owns this ticket. Defaults to CEO
    # (resolved at route-time from app.config or env). ON DELETE SET NULL
    # so an account purge doesn't orphan-cascade tickets.
    assignee_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Free-form tag array (Postgres ARRAY(TEXT)) — triage keywords like
    # 'al-form', 'mobile', 'login'. Optional; never required.
    tags = db.Column(ARRAY(db.String(64)), nullable=True)

    # Composite indexes for the two hottest access patterns:
    #   CEO inbox:        (status, priority, created_at DESC)
    #   Customer history: (user_id, created_at DESC)
    __table_args__ = (
        db.Index(
            "ix_d2_support_tickets_status_priority_created",
            "status",
            "priority",
            db.text("created_at DESC"),
        ),
        db.Index(
            "ix_d2_support_tickets_user_created",
            "user_id",
            db.text("created_at DESC"),
        ),
    )

    def __repr__(self):
        return (
            f"<D2SupportTicket {self.id} user={self.user_id} "
            f"status={self.status} priority={self.priority}>"
        )


class D2SupportTicketMessage(db.Model):
    """One reply within a ticket conversation. Append-only by convention.

    Author may be the customer, a staff member, the AI (future), or a system-
    generated note (e.g. 'Filed from feedback widget'). `author_user_id` is
    nullable so AI/system rows don't need a synthetic User account.
    """
    __tablename__ = "d2_support_ticket_messages"

    id = db.Column(db.Integer, primary_key=True)

    ticket_id = db.Column(
        db.Integer,
        db.ForeignKey("d2_support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable for ai/system rows.
    author_user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    author_role = db.Column(db.String(16), nullable=False)

    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True,
    )

    __table_args__ = (
        db.Index(
            "ix_d2_support_ticket_messages_ticket_created",
            "ticket_id",
            "created_at",
        ),
    )

    def __repr__(self):
        return (
            f"<D2SupportTicketMessage {self.id} ticket={self.ticket_id} "
            f"role={self.author_role}>"
        )


# --------------------------------------------------------------------------- #
# Belt-and-braces DDL — runs at import.
# --------------------------------------------------------------------------- #
_CREATE_TICKETS_DDL = """
CREATE TABLE IF NOT EXISTS d2_support_tickets (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    session_anon_id VARCHAR(64) NULL,
    subject VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    priority VARCHAR(16) NOT NULL DEFAULT 'normal',
    category VARCHAR(32) NULL,
    assignee_user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    tags TEXT[] NULL,
    CONSTRAINT d2_support_tickets_status_check CHECK (
        status IN ('open','awaiting_customer','resolved','closed')
    ),
    CONSTRAINT d2_support_tickets_priority_check CHECK (
        priority IN ('low','normal','high')
    ),
    CONSTRAINT d2_support_tickets_category_check CHECK (
        category IS NULL OR
        category IN ('bug','feature','confusion','praise','other')
    )
)
"""

_CREATE_TICKET_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_created_at "
    "ON d2_support_tickets (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_user_id "
    "ON d2_support_tickets (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_status "
    "ON d2_support_tickets (status)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_priority "
    "ON d2_support_tickets (priority)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_assignee_user_id "
    "ON d2_support_tickets (assignee_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_status_priority_created "
    "ON d2_support_tickets (status, priority, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_user_created "
    "ON d2_support_tickets (user_id, created_at DESC)",
]

_CREATE_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS d2_support_ticket_messages (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL
        REFERENCES d2_support_tickets(id) ON DELETE CASCADE,
    author_user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    author_role VARCHAR(16) NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT d2_support_ticket_messages_role_check CHECK (
        author_role IN ('customer','staff','ai','system')
    )
)
"""

_CREATE_MESSAGE_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_ticket_id "
    "ON d2_support_ticket_messages (ticket_id)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_author "
    "ON d2_support_ticket_messages (author_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_created_at "
    "ON d2_support_ticket_messages (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_ticket_created "
    "ON d2_support_ticket_messages (ticket_id, created_at)",
]


def _ensure_d2_support_tables():
    """Idempotent DDL. Runs on import; cheap.

    Mirrors the pattern in feedback_models, support_copilot_models,
    event_models. SQLAlchemy db.create_all() ALSO picks these models up,
    but the raw DDL guarantees the table exists by the time the first
    route hits it even when reflection ordering is unpredictable.
    """
    try:
        from sqlalchemy import text as _sql_text
        from app import app
        with app.app_context():
            db.session.execute(_sql_text(_CREATE_TICKETS_DDL))
            for ddl in _CREATE_TICKET_INDEXES_DDL:
                db.session.execute(_sql_text(ddl))
            db.session.execute(_sql_text(_CREATE_MESSAGES_DDL))
            for ddl in _CREATE_MESSAGE_INDEXES_DDL:
                db.session.execute(_sql_text(ddl))
            db.session.commit()
    except Exception as e:
        log.warning("Could not ensure d2_support_tickets tables: %s", e)
        try:
            db.session.rollback()
        except Exception:
            pass


_ensure_d2_support_tables()


__all__ = [
    "D2SupportTicket",
    "D2SupportTicketMessage",
    "TICKET_STATUSES",
    "TICKET_PRIORITIES",
    "TICKET_CATEGORIES",
    "MESSAGE_AUTHOR_ROLES",
    "_ensure_d2_support_tables",
]
