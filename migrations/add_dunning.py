"""
Migration: Create paywall_dunning table.

Tier D3 / C5 — Failed-payment dunning recovery.

Creates a new table tracking every Stripe invoice.payment_failed event so we
can (a) audit retries, (b) gate the in-app banner, (c) wire SES/Mailgun
notifications later. One row per (subscription_id, stripe_invoice_id) — the
same invoice can fail multiple Stripe retries; we update attempt_count +
last_failed_at on each retry instead of inserting duplicates.

Idempotent via CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
Rollback via DROP TABLE.

Wave 1 #C1's migrations/add_subscription_autorenew.py is the pattern.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS paywall_dunning (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL REFERENCES "user"(id),
    subscription_id     INTEGER NOT NULL REFERENCES paywall_subscription(id),
    stripe_invoice_id   VARCHAR(255) NOT NULL,
    attempt_count       INTEGER NOT NULL DEFAULT 1,
    first_failed_at     TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    last_failed_at      TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    next_retry_at       TIMESTAMP NULL,
    state               VARCHAR(16) NOT NULL DEFAULT 'pending',
    resolved_at         TIMESTAMP NULL,
    CONSTRAINT uq_paywall_dunning_sub_invoice
        UNIQUE (subscription_id, stripe_invoice_id)
);
"""

CREATE_INDEXES_DDL = """
CREATE INDEX IF NOT EXISTS ix_paywall_dunning_user_id
    ON paywall_dunning (user_id);

CREATE INDEX IF NOT EXISTS ix_paywall_dunning_subscription_id
    ON paywall_dunning (subscription_id);

CREATE INDEX IF NOT EXISTS ix_paywall_dunning_stripe_invoice_id
    ON paywall_dunning (stripe_invoice_id);

CREATE INDEX IF NOT EXISTS ix_paywall_dunning_state
    ON paywall_dunning (state);

CREATE INDEX IF NOT EXISTS ix_paywall_dunning_user_state
    ON paywall_dunning (user_id, state);
"""

DROP_DDL = """
DROP INDEX IF EXISTS ix_paywall_dunning_user_id;
DROP INDEX IF EXISTS ix_paywall_dunning_subscription_id;
DROP INDEX IF EXISTS ix_paywall_dunning_stripe_invoice_id;
DROP INDEX IF EXISTS ix_paywall_dunning_state;
DROP INDEX IF EXISTS ix_paywall_dunning_user_state;
DROP TABLE IF EXISTS paywall_dunning;
"""


def upgrade() -> bool:
    """Create paywall_dunning + indexes."""
    with app.app_context():
        try:
            log.info("add_dunning: creating paywall_dunning table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  + paywall_dunning created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed at CREATE TABLE: %s", exc)
            return False

        try:
            log.info("add_dunning: creating indexes")
            for stmt in CREATE_INDEXES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + indexes added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning("index creation non-fatal failure: %s", exc)

        log.info("add_dunning: upgrade complete")
        return True


def downgrade() -> bool:
    """Drop paywall_dunning + indexes."""
    with app.app_context():
        try:
            log.info("add_dunning: dropping paywall_dunning table + indexes")
            for stmt in DROP_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + paywall_dunning dropped (or did not exist)")
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
