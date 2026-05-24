"""
Migration: Add utm_source / utm_medium / utm_campaign / utm_term / utm_content
columns to the user table.

Tier D6 / A2 paid acquisition (2026-05-24).

Adds five nullable VARCHAR(128) columns so utm_capture.persist_to_user() can
lift the first-touch UTM tuple from session onto the User row at signup time.
This gives the funnel dashboard a lifetime attribution surface (User.utm_source)
in addition to the per-event payload attribution (events.payload->>'utm_source')
that already works via the utm_capture middleware.

Follows the same raw-SQL pattern as migrations/add_last_login_at.py:
  - Shared app + db (from main app context)
  - SQLAlchemy text()
  - Idempotent via ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS
  - Rollback-safe
  - CLI-runnable: ``python migrations/add_utm_columns_to_user.py upgrade``

DO NOT auto-run during deploy. CEO triggers post-deploy via:
  flyctl ssh console -a fiesta-mvp -C 'python migrations/add_utm_columns_to_user.py upgrade'

Downgrade: DROP COLUMN IF EXISTS x5. Safe — no FK references.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
ADD_COLUMNS_DDL = """
ALTER TABLE "user"
ADD COLUMN IF NOT EXISTS utm_source   VARCHAR(128) NULL,
ADD COLUMN IF NOT EXISTS utm_medium   VARCHAR(128) NULL,
ADD COLUMN IF NOT EXISTS utm_campaign VARCHAR(128) NULL,
ADD COLUMN IF NOT EXISTS utm_term     VARCHAR(128) NULL,
ADD COLUMN IF NOT EXISTS utm_content  VARCHAR(128) NULL;
"""

# Lightweight index on utm_source for channel-breakdown analytics. We don't
# index the other four (low cardinality / low query frequency); the existing
# events.payload->>'utm_source' partial index covers per-event attribution.
ADD_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_user_utm_source
ON "user" (utm_source)
WHERE utm_source IS NOT NULL;
"""

DROP_INDEX_DDL = """
DROP INDEX IF EXISTS ix_user_utm_source;
"""

DROP_COLUMNS_DDL = """
ALTER TABLE "user"
DROP COLUMN IF EXISTS utm_source,
DROP COLUMN IF EXISTS utm_medium,
DROP COLUMN IF EXISTS utm_campaign,
DROP COLUMN IF EXISTS utm_term,
DROP COLUMN IF EXISTS utm_content;
"""


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade():
    """Add the 5 utm_* columns + partial index on utm_source. Idempotent."""
    with app.app_context():
        try:
            log.info("Migration add_utm_columns_to_user: adding columns")
            db.session.execute(text(ADD_COLUMNS_DDL))
            db.session.commit()
            log.info("  ✓ utm_source/utm_medium/utm_campaign/utm_term/utm_content added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at ADD COLUMN: %s", exc)
            return False

        try:
            log.info("Migration add_utm_columns_to_user: adding partial index on utm_source")
            db.session.execute(text(ADD_INDEX_DDL))
            db.session.commit()
            log.info("  ✓ ix_user_utm_source added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at CREATE INDEX: %s", exc)
            # Columns are already added; partial failure is recoverable on retry.
            return False

        log.info("Migration add_utm_columns_to_user: upgrade complete")
        return True


# ---------------------------------------------------------------------------
# Downgrade (rollback)
# ---------------------------------------------------------------------------
def downgrade():
    """Drop the 5 utm_* columns + the partial index. Idempotent."""
    with app.app_context():
        try:
            log.info("Migration add_utm_columns_to_user: dropping index")
            db.session.execute(text(DROP_INDEX_DDL))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            log.warning("Migration downgrade index drop failed (non-fatal): %s", exc)

        try:
            log.info("Migration add_utm_columns_to_user: dropping columns")
            db.session.execute(text(DROP_COLUMNS_DDL))
            db.session.commit()
            log.info("  ✓ utm_* columns dropped (or did not exist)")
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
