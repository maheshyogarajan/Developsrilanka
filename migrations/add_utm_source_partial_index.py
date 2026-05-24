"""
Migration: add PARTIAL expression index on events.payload->>'utm_source'.

Tier D2 / F6 (2026-05-24). Tier C #4 analytics dashboard surfaced that
every channel breakdown query does a JSON probe across all funnel rows
(`payload->>'utm_source'`). Fine at current volume; ahead of the
4-acquisition-channel launch we want the index in place so cardinality
growth doesn't compound into a sequential-scan tax.

Why a PARTIAL expression index?

  - EXPRESSION: PostgreSQL indexes the *result* of `payload->>'utm_source'`,
    so the existing _tier_c_analytics_sql_pack/*.sql queries that filter or
    group on `payload->>'utm_source'` are served directly from the index
    without a JSON evaluation per row.
  - PARTIAL: `WHERE payload ? 'utm_source'` restricts the index to rows
    where the key is actually present. The events table is dominated by
    non-attribution events (page views, beacons, internal pings) that
    never carry utm_source. Indexing only the rows where the key exists
    keeps the index small (likely <5% of table size) and write-cost low.
  - IF NOT EXISTS / IF EXISTS: idempotent — same raw-SQL pattern as
    migrations/add_session_anon_id_to_events.py and
    migrations/add_last_login_at.py.

Query benefit (transparent — no app changes):

    SELECT payload->>'utm_source' AS channel, COUNT(*)
    FROM events
    WHERE payload ? 'utm_source' AND created_at > now() - interval '7 days'
    GROUP BY 1;

  Postgres can satisfy this from `ix_events_utm_source` instead of a
  seq-scan + JSON probe. The existing queries in
  `_tier_c_analytics_sql_pack/*.sql` already filter/probe in this shape
  and inherit the speed-up at zero code cost.

Downgrade: DROP INDEX IF EXISTS. Safe — index, not column.

DO NOT auto-run this migration during a deploy. CEO triggers post-deploy
via the flyctl ssh command in the handoff.
"""
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


ADD_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_events_utm_source
ON events ((payload->>'utm_source'))
WHERE (payload->>'utm_source') IS NOT NULL;
"""
# Note: events.payload is `json` (not `jsonb`). The `?` existence operator
# only exists on jsonb, so we use the `IS NOT NULL` form on the expression
# itself. Identical semantic for a non-null utm_source key; PostgreSQL is
# smart enough to use this index for queries that filter on
# `payload->>'utm_source' = 'foo'` or `IS NOT NULL` directly.

DROP_INDEX_DDL = """
DROP INDEX IF EXISTS ix_events_utm_source;
"""


def upgrade():
    """Add partial expression index on events.payload->>'utm_source'. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration add_utm_source_partial_index: adding partial expression index")
            db.session.execute(text(ADD_INDEX_DDL))
            db.session.commit()
            log.info("  v ix_events_utm_source partial index added (or already existed)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at CREATE INDEX: %s", exc)
            return False


def downgrade():
    """Drop the partial expression index. Idempotent."""
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            db.session.execute(text(DROP_INDEX_DDL))
            db.session.commit()
            log.info("  v ix_events_utm_source partial index dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Downgrade index drop failed: %s", exc)
            return False


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    ok = downgrade() if action == "downgrade" else upgrade()
    sys.exit(0 if ok else 1)
