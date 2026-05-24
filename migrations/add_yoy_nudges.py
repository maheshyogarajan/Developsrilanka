"""
Migration: Create yoy_nudge table (Tier D4 / C2 — YoY retention nudges).

Belt-and-braces companion to ``yoy_models.register_models()`` + the
``db.create_all()`` boot path. Either path is sufficient on a clean install;
this migration is the safe re-runnable path for prod where ``create_all`` may
already have been bypassed via gunicorn/wsgi entrypoints.

Idempotent via CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
Rollback via DROP TABLE IF EXISTS.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS yoy_nudge (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES "user"(id),
    nudge_key       VARCHAR(64) NOT NULL,
    tax_year        VARCHAR(8) NOT NULL,
    scheduled_at    TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    sent_at         TIMESTAMP NULL,
    dedup_key       VARCHAR(128) NOT NULL,
    send_status     VARCHAR(16) NOT NULL DEFAULT 'scheduled',
    send_error      VARCHAR(500) NULL
);
"""

ADD_INDEXES_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_yoy_nudge_dedup_key
    ON yoy_nudge (dedup_key);

CREATE INDEX IF NOT EXISTS ix_yoy_nudge_user_id
    ON yoy_nudge (user_id);

CREATE INDEX IF NOT EXISTS ix_yoy_nudge_nudge_key
    ON yoy_nudge (nudge_key);

CREATE INDEX IF NOT EXISTS ix_yoy_nudge_send_status
    ON yoy_nudge (send_status);
"""

DROP_DDL = """
DROP INDEX IF EXISTS ix_yoy_nudge_send_status;
DROP INDEX IF EXISTS ix_yoy_nudge_nudge_key;
DROP INDEX IF EXISTS ix_yoy_nudge_user_id;
DROP INDEX IF EXISTS ix_yoy_nudge_dedup_key;
DROP TABLE IF EXISTS yoy_nudge;
"""


def upgrade() -> bool:
    with app.app_context():
        try:
            log.info("add_yoy_nudges: creating table")
            for stmt in CREATE_TABLE_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + yoy_nudge table created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed at CREATE TABLE: %s", exc)
            return False

        try:
            log.info("add_yoy_nudges: adding indexes")
            for stmt in ADD_INDEXES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + indexes added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning("index creation non-fatal failure: %s", exc)

        log.info("add_yoy_nudges: upgrade complete")
        return True


def downgrade() -> bool:
    with app.app_context():
        try:
            log.info("add_yoy_nudges: dropping table + indexes")
            for stmt in DROP_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + yoy_nudge dropped (or did not exist)")
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
