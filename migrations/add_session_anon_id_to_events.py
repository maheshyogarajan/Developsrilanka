"""
Migration: add session_anon_id column to the events table.

Sprint 4 Tier B (2026-05-24). Pairs with `analytics_beacon_routes.py` which
mints a `session_anon_id` cookie on first request and writes it into every
event's `payload` dict via the EVENT SPINE.

Why a dedicated column rather than relying on payload[session_anon_id]?

  Wave 2 leading-indicator dashboards filter on the anon id (per-visitor
  funnel attribution before the user ever signs up). A top-level column
  with its own index is ~10-30x faster than a `payload->>'session_anon_id'`
  JSON probe under the scale we expect (60K-300K rows/month).

Follows the raw-SQL idempotent pattern of migrations/add_last_login_at.py.

Backfill: the column starts NULL on existing rows. The beacon writes the
value into both `payload['session_anon_id']` (set by the endpoint) AND
`session_anon_id` (set by a future ORM/Event-model update). This migration
deliberately does NOT alter the Event ORM model — that change ships in a
follow-up so the column is in place before any code writes to it.

Downgrade: DROP COLUMN IF EXISTS (safe — column has no FKs).

DO NOT auto-run this migration during a deploy. The Sprint 4 Tier B work
ships the column ready-to-use; the merge / deploy step is what triggers it.
"""
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


ADD_COLUMN_DDL = """
ALTER TABLE events
ADD COLUMN IF NOT EXISTS session_anon_id VARCHAR(64) NULL;
"""

ADD_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_events_anon_created_at
ON events (session_anon_id, created_at DESC);
"""

DROP_INDEX_DDL = """
DROP INDEX IF EXISTS ix_events_anon_created_at;
"""

DROP_COLUMN_DDL = """
ALTER TABLE events
DROP COLUMN IF EXISTS session_anon_id;
"""


def upgrade():
    """Add session_anon_id column + index. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration add_session_anon_id_to_events: adding column")
            db.session.execute(text(ADD_COLUMN_DDL))
            db.session.commit()
            log.info("  v session_anon_id column added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at ADD COLUMN: %s", exc)
            return False

        try:
            log.info("Migration add_session_anon_id_to_events: adding index")
            db.session.execute(text(ADD_INDEX_DDL))
            db.session.commit()
            log.info("  v ix_events_anon_created_at index added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning(
                "Migration add_session_anon_id_to_events: index step failed (non-fatal): %s",
                exc,
            )

        log.info("Migration add_session_anon_id_to_events: upgrade complete")
        return True


def downgrade():
    """Drop the column + index. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            db.session.execute(text(DROP_INDEX_DDL))
            db.session.commit()
            log.info("  v ix_events_anon_created_at index dropped (or did not exist)")
        except Exception as exc:
            db.session.rollback()
            log.warning("Downgrade index drop failed (non-fatal): %s", exc)

        try:
            db.session.execute(text(DROP_COLUMN_DDL))
            db.session.commit()
            log.info("  v session_anon_id column dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Downgrade column drop failed: %s", exc)
            return False


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    ok = downgrade() if action == "downgrade" else upgrade()
    sys.exit(0 if ok else 1)
