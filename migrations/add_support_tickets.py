"""
Migration: create the `d2_support_tickets` + `d2_support_ticket_messages` tables.

Tier D2 (2026-05-24). Pairs with `support_models.py` (ORM) and
`support_tickets_routes.py` (HTTP). Follows the raw-SQL idempotent pattern
of migrations/add_feedback_table.py.

The app boots fine without this migration — `support_models._ensure_d2_support_tables()`
runs at import and self-heals. This migration exists for ops who want an
explicit, audit-logged upgrade step.

Why a new pair of tables and NOT extending the Wave 3.2 `support_tickets`
table? See the docstring of `support_models.py` — short version: the Wave 3.2
table is a flat single-shot Q&A row owned by the AI Copilot; D2 needs a
conversation thread (1:many) and additional columns (priority/category/tags/
assignee). Mutating the live Copilot table would couple two independent
lifecycles. The bridge between them is at the application layer
(feedback_routes auto-creates a D2 ticket on bug/confusion feedback).

Downgrade: DROP TABLE IF EXISTS d2_support_ticket_messages CASCADE then
d2_support_tickets CASCADE. Safe — no FKs IN to these tables; FKs OUT are
ON DELETE SET NULL / CASCADE so dropping won't cascade-delete users.
"""
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TICKETS_DDL = """
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
);
"""

CREATE_MESSAGES_DDL = """
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
);
"""

CREATE_INDEXES_DDL = [
    # Tickets table
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_created_at "
    "ON d2_support_tickets (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_user_id "
    "ON d2_support_tickets (user_id);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_status "
    "ON d2_support_tickets (status);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_priority "
    "ON d2_support_tickets (priority);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_assignee_user_id "
    "ON d2_support_tickets (assignee_user_id);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_status_priority_created "
    "ON d2_support_tickets (status, priority, created_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_tickets_user_created "
    "ON d2_support_tickets (user_id, created_at DESC);",
    # Messages table
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_ticket_id "
    "ON d2_support_ticket_messages (ticket_id);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_author "
    "ON d2_support_ticket_messages (author_user_id);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_created_at "
    "ON d2_support_ticket_messages (created_at);",
    "CREATE INDEX IF NOT EXISTS ix_d2_support_ticket_messages_ticket_created "
    "ON d2_support_ticket_messages (ticket_id, created_at);",
]

DROP_DDL = [
    "DROP TABLE IF EXISTS d2_support_ticket_messages CASCADE;",
    "DROP TABLE IF EXISTS d2_support_tickets CASCADE;",
]


def upgrade():
    """Create both tables + indexes. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration add_support_tickets: creating d2_support_tickets")
            db.session.execute(text(CREATE_TICKETS_DDL))
            db.session.commit()
            log.info("  v d2_support_tickets created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at CREATE d2_support_tickets: %s", exc)
            return False

        try:
            log.info("Migration add_support_tickets: creating d2_support_ticket_messages")
            db.session.execute(text(CREATE_MESSAGES_DDL))
            db.session.commit()
            log.info("  v d2_support_ticket_messages created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error(
                "Migration upgrade failed at CREATE d2_support_ticket_messages: %s", exc
            )
            return False

        for ddl in CREATE_INDEXES_DDL:
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                log.warning(
                    "Migration add_support_tickets: index step failed (non-fatal): %s",
                    exc,
                )

        log.info("Migration add_support_tickets: upgrade complete")
        return True


def downgrade():
    """Drop both tables. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        for ddl in DROP_DDL:
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                log.error("Downgrade failed at %s: %s", ddl, exc)
                return False
        log.info("Migration add_support_tickets: downgrade complete")
        return True


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    ok = downgrade() if action == "downgrade" else upgrade()
    sys.exit(0 if ok else 1)
