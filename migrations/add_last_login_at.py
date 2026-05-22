"""
Migration: Add last_login_at column to the user table.

Feature C8 F8.7 (PLAN_X9_COMPLETION §4 Stage C.2).

Follows the same raw-SQL pattern as migrations/add_al_tables.py
(shared app + db, SQLAlchemy text(), rollback-safe, idempotent via
ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS).

Changes:
  1. ALTER TABLE "user" ADD COLUMN IF NOT EXISTS last_login_at
     TIMESTAMP WITH TIME ZONE NULL
  2. Backfill: set last_login_at = most recent auth_login event
     per user (best-effort; leaves NULL if no event exists).

Downgrade: DROP COLUMN IF EXISTS (safe — no FK from other tables).
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
ADD_COLUMN_DDL = """
ALTER TABLE "user"
ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP WITH TIME ZONE NULL;
"""

BACKFILL_SQL = """
UPDATE "user" u
SET last_login_at = e.latest_login
FROM (
    SELECT user_id, MAX(created_at) AS latest_login
    FROM event
    WHERE event_type = 'auth_login'
    GROUP BY user_id
) e
WHERE e.user_id = u.id
  AND (u.last_login_at IS NULL OR e.latest_login > u.last_login_at);
"""

DROP_COLUMN_DDL = """
ALTER TABLE "user"
DROP COLUMN IF EXISTS last_login_at;
"""


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade():
    """Add last_login_at column and backfill from events table (idempotent)."""
    with app.app_context():
        # Step 1: add column
        try:
            log.info("Migration add_last_login_at: adding column")
            db.session.execute(text(ADD_COLUMN_DDL))
            db.session.commit()
            log.info("  ✓ last_login_at column added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at ADD COLUMN: %s", exc)
            return False

        # Step 2: backfill from events table (best-effort; table may not exist)
        try:
            log.info("Migration add_last_login_at: backfilling from events table")
            # Check if the event table exists before attempting backfill
            result = db.session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'event');"
                )
            )
            event_table_exists = result.scalar()

            if event_table_exists:
                db.session.execute(text(BACKFILL_SQL))
                db.session.commit()
                log.info("  ✓ last_login_at backfilled from event table")
            else:
                log.info(
                    "  — event table not found; skipping backfill "
                    "(last_login_at will be populated by live auth handlers)"
                )
        except Exception as exc:
            db.session.rollback()
            # Backfill failure is non-fatal — column was already added.
            log.warning(
                "Migration add_last_login_at: backfill step failed (non-fatal): %s",
                exc,
            )

        log.info("Migration add_last_login_at: upgrade complete")
        return True


# ---------------------------------------------------------------------------
# Downgrade (rollback)
# ---------------------------------------------------------------------------
def downgrade():
    """Drop last_login_at column (idempotent via IF EXISTS)."""
    with app.app_context():
        try:
            log.info("Migration add_last_login_at: dropping column")
            db.session.execute(text(DROP_COLUMN_DDL))
            db.session.commit()
            log.info("  ✓ last_login_at column dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration downgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        ok = downgrade()
    else:
        ok = upgrade()
    sys.exit(0 if ok else 1)
