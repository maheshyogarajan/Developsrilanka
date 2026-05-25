"""
Migration M3-001 — MS3 E.1 B12 Business Income.

Creates the business-income canonical tables + adds the soft-link FK to
``incomes.business_income_id`` (Design Lock 2 §3 + §4 forward-compat).

What this migration does (additive only — no column drops, no incompatible
type changes; safe to run on prod without downtime):

  1. Create table ``business_income_entries`` — one row per
     (user, tax_year, business_name). 1:1 with an Income row that holds
     the gross-receipts Money.
  2. Create table ``business_expense_entries`` — 1:N off
     business_income_entries. Holds Money flat columns + category +
     description + date_incurred.
  3. ALTER ``incomes`` ADD ``business_income_id`` FK →
     ``business_income_entries.id`` (nullable; populated by
     fiesta.tax.business_income.record_business_income()).
  4. Create indexes on both new tables.

Dialect-aware: Postgres prod (Fly/Neon) uses ``ADD COLUMN IF NOT EXISTS`` +
``CREATE TABLE IF NOT EXISTS``. SQLite (test) introspects ``PRAGMA
table_info`` before each ALTER. Both paths are idempotent.

Run::

    python migrations/20260525_140100_f_b12_business.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_140100_f_b12_business.py upgrade'

Downgrade reverses tables + columns (use only when reverting MS3 E.1)::

    python migrations/20260525_140100_f_b12_business.py downgrade

Provenance: Inventory §B12 + Design Lock 2 §3/§4 + IRA §6.
"""
from __future__ import annotations

import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app import app, db  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M3-001")


# ---------------------------------------------------------------------------
# Dialect introspection helpers
# ---------------------------------------------------------------------------
def _dialect() -> str:
    return db.engine.dialect.name.lower()


def _table_exists(table: str) -> bool:
    insp = db.inspect(db.engine)
    return insp.has_table(table)


def _column_exists(table: str, column: str) -> bool:
    insp = db.inspect(db.engine)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


# ---------------------------------------------------------------------------
# DDL — business_income_entries
# ---------------------------------------------------------------------------
def _ddl_business_income_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS business_income_entries (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL
                            REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year        VARCHAR(7) NOT NULL,
            business_name   VARCHAR(128) NOT NULL,
            business_type   VARCHAR(16) NOT NULL DEFAULT 'sole_prop',
            source_country  VARCHAR(2),
            income_id       INTEGER
                            REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    # SQLite
    return """
    CREATE TABLE IF NOT EXISTS business_income_entries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year        VARCHAR(7) NOT NULL,
        business_name   VARCHAR(128) NOT NULL,
        business_type   VARCHAR(16) NOT NULL DEFAULT 'sole_prop',
        source_country  VARCHAR(2),
        income_id       INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs   JSON NOT NULL DEFAULT '[]',
        created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


# ---------------------------------------------------------------------------
# DDL — business_expense_entries
# ---------------------------------------------------------------------------
def _ddl_business_expense_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS business_expense_entries (
            id                  SERIAL PRIMARY KEY,
            business_income_id  INTEGER NOT NULL
                                REFERENCES business_income_entries(id) ON DELETE CASCADE,
            amount              NUMERIC(20, 4) NOT NULL,
            currency            VARCHAR(3) NOT NULL DEFAULT 'LKR',
            fx_rate             NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
            fx_source           VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
            fx_date             DATE NOT NULL,
            amount_lkr          NUMERIC(20, 2) NOT NULL,
            category            VARCHAR(32) NOT NULL DEFAULT 'other',
            description         VARCHAR(512),
            date_incurred       DATE NOT NULL,
            evidence_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS business_expense_entries (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        business_income_id  INTEGER NOT NULL
                            REFERENCES business_income_entries(id) ON DELETE CASCADE,
        amount              NUMERIC(20, 4) NOT NULL,
        currency            VARCHAR(3) NOT NULL DEFAULT 'LKR',
        fx_rate             NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
        fx_source           VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
        fx_date             DATE NOT NULL,
        amount_lkr          NUMERIC(20, 2) NOT NULL,
        category            VARCHAR(32) NOT NULL DEFAULT 'other',
        description         VARCHAR(512),
        date_incurred       DATE NOT NULL,
        evidence_refs       JSON NOT NULL DEFAULT '[]',
        created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


_INDEXES = (
    ("ix_business_income_entries_user_id", "business_income_entries", "user_id"),
    ("ix_business_income_entries_tax_year", "business_income_entries", "tax_year"),
    ("ix_business_income_entries_income_id", "business_income_entries", "income_id"),
    ("ix_business_expense_entries_business_id",
     "business_expense_entries", "business_income_id"),
    ("ix_incomes_business_income_id", "incomes", "business_income_id"),
)

_COMPOSITE_INDEXES = (
    ("ix_business_income_entries_user_tax_year",
     "business_income_entries", ("user_id", "tax_year")),
    ("ix_business_income_entries_user_tax_year_name",
     "business_income_entries", ("user_id", "tax_year", "business_name")),
    ("ix_business_expense_entries_business_id_date",
     "business_expense_entries", ("business_income_id", "date_incurred")),
)


def _create_indexes() -> None:
    for name, table, col in _INDEXES:
        db.session.execute(
            text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col});")
        )
    for name, table, cols in _COMPOSITE_INDEXES:
        col_list = ", ".join(cols)
        db.session.execute(
            text(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({col_list});")
        )
    db.session.commit()


# ---------------------------------------------------------------------------
# ALTER incomes ADD business_income_id (dialect-aware, idempotent)
# ---------------------------------------------------------------------------
def _add_income_business_id() -> None:
    if _column_exists("incomes", "business_income_id"):
        log.info("  step skip: incomes.business_income_id already exists")
        return
    if _dialect() == "postgresql":
        sql = (
            "ALTER TABLE incomes "
            "ADD COLUMN IF NOT EXISTS business_income_id INTEGER "
            "REFERENCES business_income_entries(id) ON DELETE SET NULL;"
        )
    else:
        sql = (
            "ALTER TABLE incomes "
            "ADD COLUMN business_income_id INTEGER "
            "REFERENCES business_income_entries(id) ON DELETE SET NULL;"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: incomes.business_income_id added")


def _verify_required_tables() -> bool:
    """Confirm M2-001 has been run before B12 activates."""
    insp = db.inspect(db.engine)
    if not insp.has_table("incomes"):
        log.error(
            "M3-001 cannot proceed; missing prerequisite table 'incomes'. "
            "Run M2-001 (20260525_130100_e_b8_schema.py upgrade) first."
        )
        return False
    log.info("M3-001 prerequisites OK: incomes present")
    return True


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply all schema changes. Idempotent."""
    with app.app_context():
        log.info(
            "=== M3-001 B12 business income: UPGRADE starting (dialect=%s) ===",
            _dialect(),
        )
        if not _verify_required_tables():
            log.info("=== M3-001 B12 business income: UPGRADE blocked ===")
            return False

        ok = True

        # Step 1-2: create tables
        for label, ddl in (
            ("create business_income_entries", _ddl_business_income_entries()),
            ("create business_expense_entries", _ddl_business_expense_entries()),
        ):
            try:
                log.info("M3-001 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        # Step 3: ALTER incomes ADD business_income_id
        try:
            log.info("M3-001 step: add incomes.business_income_id")
            _add_income_business_id()
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (incomes.business_income_id): %s", exc)
            ok = False

        # Step 4: indexes
        try:
            log.info("M3-001 step: create indexes")
            _create_indexes()
            log.info("  step ok: indexes")
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (indexes): %s", exc)
            ok = False

        log.info(
            "=== M3-001 B12 business income: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)",
        )
        return ok


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------
def downgrade() -> bool:
    """Reverse all schema changes. Only safe when reverting MS3 E.1."""
    with app.app_context():
        log.info("=== M3-001 B12 business income: DOWNGRADE starting ===")
        ok = True

        drops = [
            ("drop incomes.business_income_id",
             "ALTER TABLE incomes DROP COLUMN IF EXISTS business_income_id;"
             if _dialect() == "postgresql" else
             "ALTER TABLE incomes DROP COLUMN business_income_id;"),
            ("drop business_expense_entries",
             "DROP TABLE IF EXISTS business_expense_entries;"),
            ("drop business_income_entries",
             "DROP TABLE IF EXISTS business_income_entries;"),
        ]
        for label, ddl in drops:
            try:
                log.info("M3-001 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== M3-001 B12 business income: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL",
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
