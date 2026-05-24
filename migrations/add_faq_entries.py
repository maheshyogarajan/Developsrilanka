"""
Migration: create the `faq_entries` table.

Sprint 4 Tier D3 (2026-05-24). Pairs with `faq_models.py` (ORM),
`faq_routes.py` (/help, /help/<slug>, /admin/faq, /sitemap.xml), and
`faq_autogen.py` (weekly clustering Celery beat task).

Follows the same idempotent raw-SQL pattern as `add_feedback_table.py`.

The app boots fine without this migration — `app._ensure_additive_schema()`
includes the equivalent `CREATE TABLE IF NOT EXISTS faq_entries (...)` so
every entry point (gunicorn, wsgi.py, celery) self-heals. This migration
exists for ops who want an explicit, audit-logged upgrade step.

Downgrade: DROP TABLE IF EXISTS faq_entries CASCADE (safe — no FKs IN to
this table, no FKs OUT).
"""
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS faq_entries (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    slug VARCHAR(160) NOT NULL UNIQUE,
    question VARCHAR(200) NOT NULL,
    answer TEXT NOT NULL DEFAULT '',
    category VARCHAR(32) NOT NULL DEFAULT 'general',
    source VARCHAR(32) NOT NULL DEFAULT 'manual',
    view_count INTEGER NOT NULL DEFAULT 0,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT faq_entries_category_check CHECK (
        category IN ('foreign_income','deductions','filing','payment','general')
    ),
    CONSTRAINT faq_entries_source_check CHECK (
        source IN ('manual','auto_from_feedback','auto_from_qa')
    )
);
"""

CREATE_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS ix_faq_entries_slug ON faq_entries (slug);",
    "CREATE INDEX IF NOT EXISTS ix_faq_entries_created_at ON faq_entries (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_faq_entries_category ON faq_entries (category);",
    "CREATE INDEX IF NOT EXISTS ix_faq_entries_is_published ON faq_entries (is_published);",
    # Composite — powers the /help index query
    # (published list, newest or most-viewed first).
    "CREATE INDEX IF NOT EXISTS ix_faq_entries_pub_category_created "
    "ON faq_entries (is_published, category, created_at DESC);",
]

DROP_TABLE_DDL = "DROP TABLE IF EXISTS faq_entries;"


def upgrade():
    """Create the faq_entries table + indexes. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration add_faq_entries: creating table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  v faq_entries table created (or already existed)")
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
                    "Migration add_faq_entries: index step failed "
                    "(non-fatal): %s",
                    exc,
                )

        log.info("Migration add_faq_entries: upgrade complete")
        return True


def downgrade():
    """Drop the faq_entries table. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            db.session.execute(text(DROP_TABLE_DDL))
            db.session.commit()
            log.info("  v faq_entries table dropped (or did not exist)")
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
