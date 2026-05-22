"""
Migration: Add fiesta_asset_entry + fiesta_liability_entry tables.

Feature 9 D6 (PLAN_X9_COMPLETION §5 Stage D — A&L declaration tracker).

Follows the same raw-SQL pattern as migrations/add_tax_deductibility_fields.py
(shared app + db, SQLAlchemy text(), rollback-safe, idempotent via
CREATE TABLE IF NOT EXISTS / DROP TABLE IF EXISTS).

Tables created:
  fiesta_asset_entry
  fiesta_liability_entry

Downgrade: DROP TABLE IF EXISTS (safe — no FK from other tables into these).
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------
ASSET_DDL = """
CREATE TABLE IF NOT EXISTS fiesta_asset_entry (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    tax_year            VARCHAR(16) NOT NULL,
    category            VARCHAR(48) NOT NULL,
    description         VARCHAR(512) NOT NULL,
    value_lkr_cents     INTEGER NOT NULL DEFAULT 0,
    acquired_date       DATE,
    evidence_ref        VARCHAR(256),
    fa_submission_id    VARCHAR(64),
    created_at          TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
"""

ASSET_IDX = """
CREATE INDEX IF NOT EXISTS ix_fiesta_asset_entry_user_year
    ON fiesta_asset_entry (user_id, tax_year);
"""

LIABILITY_DDL = """
CREATE TABLE IF NOT EXISTS fiesta_liability_entry (
    id                          SERIAL PRIMARY KEY,
    user_id                     INTEGER NOT NULL,
    tax_year                    VARCHAR(16) NOT NULL,
    category                    VARCHAR(48) NOT NULL,
    description                 VARCHAR(512) NOT NULL,
    lender                      VARCHAR(256),
    balance_lkr_cents           INTEGER NOT NULL DEFAULT 0,
    original_amount_lkr_cents   INTEGER,
    due_date                    DATE,
    created_at                  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
"""

LIABILITY_IDX = """
CREATE INDEX IF NOT EXISTS ix_fiesta_liability_entry_user_year
    ON fiesta_liability_entry (user_id, tax_year);
"""


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade():
    """Create both A&L tables (idempotent — IF NOT EXISTS)."""
    with app.app_context():
        try:
            log.info("Migration add_al_tables: starting upgrade")
            for label, sql in [
                ("fiesta_asset_entry table", ASSET_DDL),
                ("fiesta_asset_entry index", ASSET_IDX),
                ("fiesta_liability_entry table", LIABILITY_DDL),
                ("fiesta_liability_entry index", LIABILITY_IDX),
            ]:
                log.info("  → %s", label)
                db.session.execute(text(sql))
                db.session.commit()
                log.info("  ✓ %s", label)
            log.info("Migration add_al_tables: upgrade complete")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Downgrade (rollback)
# ---------------------------------------------------------------------------
def downgrade():
    """Drop both A&L tables (safe — no incoming FKs)."""
    with app.app_context():
        try:
            log.info("Migration add_al_tables: starting downgrade")
            for label, sql in [
                ("fiesta_liability_entry", "DROP TABLE IF EXISTS fiesta_liability_entry;"),
                ("fiesta_asset_entry", "DROP TABLE IF EXISTS fiesta_asset_entry;"),
            ]:
                log.info("  → drop %s", label)
                db.session.execute(text(sql))
                db.session.commit()
                log.info("  ✓ dropped %s", label)
            log.info("Migration add_al_tables: downgrade complete")
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
