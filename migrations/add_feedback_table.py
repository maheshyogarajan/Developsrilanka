"""
Migration: create the `feedback` table.

Sprint 4 Tier D4 (2026-05-24). Pairs with `feedback_models.py` (ORM) and
`feedback_routes.py` (POST /api/feedback). Follows the raw-SQL idempotent
pattern of migrations/add_session_anon_id_to_events.py.

The app boots fine without this migration — `app._ensure_additive_schema()`
includes a `CREATE TABLE IF NOT EXISTS feedback (...)` so every entry point
(gunicorn, wsgi.py, celery) self-heals. This migration exists for ops who
want an explicit, audit-logged upgrade step.

Downgrade: DROP TABLE IF EXISTS feedback CASCADE (safe — no FKs IN to this
table; the FK to user.id is OUT and uses ON DELETE SET NULL so dropping
this table won't cascade-delete users).
"""
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS feedback (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES "user"(id) ON DELETE SET NULL,
    session_anon_id VARCHAR(64) NULL,
    category VARCHAR(32) NOT NULL,
    body TEXT NOT NULL,
    url_at_submit VARCHAR(512) NULL,
    user_agent TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT feedback_category_check CHECK (
        category IN ('bug','feature','confusion','praise','other')
    )
);
"""

CREATE_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_feedback_user_id ON feedback (user_id);",
    "CREATE INDEX IF NOT EXISTS ix_feedback_anon ON feedback (session_anon_id);",
    "CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON feedback (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_feedback_category_created_at "
    "ON feedback (category, created_at DESC);",
]

DROP_TABLE_DDL = "DROP TABLE IF EXISTS feedback;"


def upgrade():
    """Create the feedback table + indexes. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration add_feedback_table: creating table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  v feedback table created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at CREATE TABLE: %s", exc)
            return False

        for ddl in CREATE_INDEXES_DDL:
            try:
                db.session.execute(text(ddl))
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                log.warning(
                    "Migration add_feedback_table: index step failed "
                    "(non-fatal): %s",
                    exc,
                )

        log.info("Migration add_feedback_table: upgrade complete")
        return True


def downgrade():
    """Drop the feedback table. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            db.session.execute(text(DROP_TABLE_DDL))
            db.session.commit()
            log.info("  v feedback table dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Downgrade failed at DROP TABLE: %s", exc)
            return False


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    ok = downgrade() if action == "downgrade" else upgrade()
    sys.exit(0 if ok else 1)
