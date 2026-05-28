"""Migration: add tax_bill_acknowledgement table.

F6.3 launch-gate (council brief 2026-05-28, plan LAUNCH_PLAN_2026-05-29.html).
Stores one row per (user_id, tax_year_s4) when the user dismisses the
interstitial on /tax-bill/<tax_year>.

Idempotent: CREATE TABLE IF NOT EXISTS + CREATE UNIQUE INDEX IF NOT EXISTS.
Follows the raw-SQL pattern of migrations/add_session_anon_id_to_events.py.

Run:
    python migrations/20260528_180000_add_tax_bill_acknowledgement.py upgrade

Downgrade:
    python migrations/20260528_180000_add_tax_bill_acknowledgement.py downgrade
"""
import logging
import sys

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tax_bill_acknowledgement (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES "user"(id),
    tax_year_s4     VARCHAR(8) NOT NULL,
    acknowledged_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    client_ip       VARCHAR(64) NULL,
    user_agent      VARCHAR(512) NULL
);
"""

CREATE_UNIQUE_INDEX_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_bill_ack_user_year
    ON tax_bill_acknowledgement (user_id, tax_year_s4);
"""

CREATE_USER_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_tax_bill_ack_user_id
    ON tax_bill_acknowledgement (user_id);
"""

DROP_TABLE_DDL = "DROP TABLE IF EXISTS tax_bill_acknowledgement;"


def upgrade() -> bool:
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            log.info("Migration f6.3: creating tax_bill_acknowledgement table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  v table created (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed at CREATE TABLE: %s", exc)
            return False

        try:
            db.session.execute(text(CREATE_UNIQUE_INDEX_DDL))
            db.session.commit()
            log.info("  v unique (user_id, tax_year_s4) index added")
        except Exception as exc:
            db.session.rollback()
            log.warning("  ! unique index step failed (non-fatal): %s", exc)

        try:
            db.session.execute(text(CREATE_USER_INDEX_DDL))
            db.session.commit()
            log.info("  v user_id index added")
        except Exception as exc:
            db.session.rollback()
            log.warning("  ! user_id index step failed (non-fatal): %s", exc)

        log.info("Migration f6.3: upgrade complete")
        return True


def downgrade() -> bool:
    from app import app, db
    from sqlalchemy import text

    with app.app_context():
        try:
            db.session.execute(text(DROP_TABLE_DDL))
            db.session.commit()
            log.info("  v tax_bill_acknowledgement dropped")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Downgrade failed: %s", exc)
            return False


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    ok = downgrade() if action == "downgrade" else upgrade()
    sys.exit(0 if ok else 1)
