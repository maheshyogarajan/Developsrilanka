"""
Migration: Create ab_experiment + ab_assignment tables.

Tier D5 / E6 — A/B testing harness (2026-05-24).

Idempotent via CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS,
matching the pattern of migrations/add_lifecycle_drip.py and
migrations/add_dunning.py.

Why two creation paths (this migration + db.create_all() in main.py):
the ORM-level create_all() picks up the models when ab_test_models is
imported, but raw DDL here guarantees the tables exist for environments
that don't go through main.py (gunicorn workers, celery, wsgi.py).
"""

import logging

from sqlalchemy import text

from app import app, db


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS ab_experiment (
    id              SERIAL PRIMARY KEY,
    key             VARCHAR(64) NOT NULL UNIQUE,
    name            VARCHAR(200) NOT NULL,
    description     TEXT NULL,
    variants        JSON NOT NULL,
    weights         JSON NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    primary_metric  VARCHAR(64) NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    started_at      TIMESTAMP NULL,
    concluded_at    TIMESTAMP NULL,
    winner_variant  VARCHAR(64) NULL
);

CREATE TABLE IF NOT EXISTS ab_assignment (
    id               SERIAL PRIMARY KEY,
    experiment_key   VARCHAR(64) NOT NULL,
    user_id          INTEGER NULL REFERENCES "user"(id) ON DELETE SET NULL,
    session_anon_id  VARCHAR(64) NULL,
    variant          VARCHAR(64) NOT NULL,
    assigned_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    CONSTRAINT uq_ab_assignment
        UNIQUE (experiment_key, user_id, session_anon_id)
);
"""

CREATE_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS ix_ab_experiment_status
    ON ab_experiment (status);

CREATE INDEX IF NOT EXISTS ix_ab_assignment_experiment_key
    ON ab_assignment (experiment_key);

CREATE INDEX IF NOT EXISTS ix_ab_assignment_user_id
    ON ab_assignment (user_id);

CREATE INDEX IF NOT EXISTS ix_ab_assignment_session_anon_id
    ON ab_assignment (session_anon_id);
"""

DROP_DDL = """
DROP INDEX IF EXISTS ix_ab_assignment_session_anon_id;
DROP INDEX IF EXISTS ix_ab_assignment_user_id;
DROP INDEX IF EXISTS ix_ab_assignment_experiment_key;
DROP INDEX IF EXISTS ix_ab_experiment_status;
DROP TABLE IF EXISTS ab_assignment;
DROP TABLE IF EXISTS ab_experiment;
"""


def upgrade() -> bool:
    """Create ab_experiment + ab_assignment + indexes."""
    with app.app_context():
        try:
            log.info("add_ab_tests: creating ab_experiment + ab_assignment")
            for stmt in CREATE_TABLE_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + tables created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed at CREATE TABLE: %s", exc)
            return False

        try:
            log.info("add_ab_tests: creating indexes")
            for stmt in CREATE_INDEXES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + indexes added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning("index creation non-fatal failure: %s", exc)

        log.info("add_ab_tests: upgrade complete")
        return True


def downgrade() -> bool:
    """Drop ab_assignment + ab_experiment + indexes."""
    with app.app_context():
        try:
            log.info("add_ab_tests: dropping ab_experiment + ab_assignment")
            for stmt in DROP_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + tables dropped (or did not exist)")
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
