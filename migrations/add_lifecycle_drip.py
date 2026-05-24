"""
Migration: Create lifecycle_email table.

Tier D4 / A5 — Lifecycle email drip (5-email sequence).

Tracks scheduled + sent drip emails. UNIQUE on
(user_id, email_key, cohort_id) so the same user can receive the same
deadline reminder in a NEW tax year (new cohort_id) without dedup
collision, but can never receive the same email twice in the same cycle.

Pattern mirrors migrations/add_dunning.py. Idempotent
via CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS lifecycle_email (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES "user"(id),
    email_key       VARCHAR(64) NOT NULL,
    cohort_id       VARCHAR(16) NOT NULL,
    scheduled_at    TIMESTAMP NOT NULL,
    sent_at         TIMESTAMP NULL,
    send_status     VARCHAR(16) NOT NULL DEFAULT 'pending',
    failure_reason  VARCHAR(512) NULL,
    context_json    TEXT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    CONSTRAINT uq_lifecycle_email_user_key_cohort
        UNIQUE (user_id, email_key, cohort_id)
);
"""

CREATE_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS ix_lifecycle_email_user_id
    ON lifecycle_email (user_id);

CREATE INDEX IF NOT EXISTS ix_lifecycle_email_email_key
    ON lifecycle_email (email_key);

CREATE INDEX IF NOT EXISTS ix_lifecycle_email_cohort_id
    ON lifecycle_email (cohort_id);

CREATE INDEX IF NOT EXISTS ix_lifecycle_email_scheduled_at
    ON lifecycle_email (scheduled_at);

CREATE INDEX IF NOT EXISTS ix_lifecycle_email_send_status
    ON lifecycle_email (send_status);

CREATE INDEX IF NOT EXISTS ix_lifecycle_email_pending_scan
    ON lifecycle_email (send_status, scheduled_at)
    WHERE send_status = 'pending';
"""

DROP_DDL = """
DROP INDEX IF EXISTS ix_lifecycle_email_pending_scan;
DROP INDEX IF EXISTS ix_lifecycle_email_send_status;
DROP INDEX IF EXISTS ix_lifecycle_email_scheduled_at;
DROP INDEX IF EXISTS ix_lifecycle_email_cohort_id;
DROP INDEX IF EXISTS ix_lifecycle_email_email_key;
DROP INDEX IF EXISTS ix_lifecycle_email_user_id;
DROP TABLE IF EXISTS lifecycle_email;
"""


def upgrade() -> bool:
    """Create lifecycle_email + indexes."""
    with app.app_context():
        try:
            log.info("add_lifecycle_drip: creating lifecycle_email table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  + lifecycle_email created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed at CREATE TABLE: %s", exc)
            return False

        try:
            log.info("add_lifecycle_drip: creating indexes")
            for stmt in CREATE_INDEXES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + indexes added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning("index creation non-fatal failure: %s", exc)

        log.info("add_lifecycle_drip: upgrade complete")
        return True


def downgrade() -> bool:
    """Drop lifecycle_email + indexes."""
    with app.app_context():
        try:
            log.info("add_lifecycle_drip: dropping lifecycle_email table + indexes")
            for stmt in DROP_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + lifecycle_email dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("downgrade failed: %s", exc)
            return False


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        ok = downgrade()
    else:
        ok = upgrade()
    sys.exit(0 if ok else 1)
