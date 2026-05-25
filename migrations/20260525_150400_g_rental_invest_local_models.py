"""
Migration MG-003-W3c — MS4 W3c G3.4 LKR Rental Income + G3.5 LOCAL Investment.

Creates the rental + local-investment canonical tables and adds the soft-link
FK to ``incomes.rental_income_id`` (Design Lock 2 §3 + §4 forward-compat).

Filename slot chosen distinctly from the parallel W3b dispatch (which owns
the canonical MG-003 slot for G3.1 employment + G3.2 professional fees) per
the W3c dispatch coordination note — to avoid collision when W3b + W3c
land on main concurrently.

What this migration does (additive only — no column drops, no incompatible
type changes; safe to run on prod without downtime):

  1. Create table ``rental_income_entries`` — one row per
     (user, tax_year, property_address, period_start). 1:1 with an Income
     row that holds the gross-rent Money.
  2. Create table ``rental_deduction_entries`` — 1:N off
     rental_income_entries. Holds Money flat columns + category +
     description + date_incurred.
  3. Create table ``local_fd_interest_entries`` — one row per
     (user, fd_account_ref, tax_year). 1:1 with an Income row holding
     gross interest.
  4. Create table ``local_dividend_entries`` — one row per
     (user, company_name, ex_dividend_date). 1:1 with an Income row
     holding gross dividend.
  5. ALTER ``incomes`` ADD ``rental_income_id`` FK →
     ``rental_income_entries.id`` (nullable; populated by
     fiesta.tax.rental_lkr.record_rental_income()).
  6. Create indexes on all new tables.

Local CGT for real_estate / equity / unit_trust uses the canonical
``asset_disposals`` table (created by M2-001) with
``asset_type IN ('real_estate', 'equity', 'unit_trust')`` and
``source_country='LK'``. No DDL needed for those — the table already exists
from B8 schema-first.

Dialect-aware: Postgres prod (Fly/Neon) uses ``ADD COLUMN IF NOT EXISTS`` +
``CREATE TABLE IF NOT EXISTS``. SQLite (test) introspects ``PRAGMA
table_info`` before each ALTER. Both paths are idempotent.

Run::

    python migrations/20260525_150400_g_rental_invest_local_models.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_150400_g_rental_invest_local_models.py upgrade'

Downgrade reverses tables + columns (use only when reverting MS4 W3c)::

    python migrations/20260525_150400_g_rental_invest_local_models.py downgrade

Provenance: Inventory §G3.4 + §G3.5 LOCAL + Design Lock 2 §3/§4/§5 +
IRA §7(2)(a) + §36 + §37 (verified 2026-05-25 via mcp__ira__get_section).
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
log = logging.getLogger("MG-003-W3c")


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
# DDL — rental_income_entries
# ---------------------------------------------------------------------------
def _ddl_rental_income_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS rental_income_entries (
            id                SERIAL PRIMARY KEY,
            user_id           INTEGER NOT NULL
                              REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year          VARCHAR(7) NOT NULL,
            property_address  VARCHAR(256) NOT NULL,
            tenant_name       VARCHAR(128),
            period_start      DATE NOT NULL,
            period_end        DATE,
            source_country    VARCHAR(2) DEFAULT 'LK',
            income_id         INTEGER
                              REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs     JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at        TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS rental_income_entries (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year          VARCHAR(7) NOT NULL,
        property_address  VARCHAR(256) NOT NULL,
        tenant_name       VARCHAR(128),
        period_start      DATE NOT NULL,
        period_end        DATE,
        source_country    VARCHAR(2) DEFAULT 'LK',
        income_id         INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs     JSON NOT NULL DEFAULT '[]',
        created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


# ---------------------------------------------------------------------------
# DDL — rental_deduction_entries
# ---------------------------------------------------------------------------
def _ddl_rental_deduction_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS rental_deduction_entries (
            id                  SERIAL PRIMARY KEY,
            rental_income_id    INTEGER NOT NULL
                                REFERENCES rental_income_entries(id) ON DELETE CASCADE,
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
    CREATE TABLE IF NOT EXISTS rental_deduction_entries (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        rental_income_id    INTEGER NOT NULL
                            REFERENCES rental_income_entries(id) ON DELETE CASCADE,
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


# ---------------------------------------------------------------------------
# DDL — local_fd_interest_entries
# ---------------------------------------------------------------------------
def _ddl_local_fd_interest_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS local_fd_interest_entries (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL
                            REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year        VARCHAR(7) NOT NULL,
            bank_name       VARCHAR(128) NOT NULL,
            fd_account_ref  VARCHAR(64),
            principal_lkr   NUMERIC(20, 2) NOT NULL DEFAULT 0,
            wht_lkr         NUMERIC(20, 2) NOT NULL DEFAULT 0,
            interest_date   DATE NOT NULL,
            income_id       INTEGER
                            REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS local_fd_interest_entries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year        VARCHAR(7) NOT NULL,
        bank_name       VARCHAR(128) NOT NULL,
        fd_account_ref  VARCHAR(64),
        principal_lkr   NUMERIC(20, 2) NOT NULL DEFAULT 0,
        wht_lkr         NUMERIC(20, 2) NOT NULL DEFAULT 0,
        interest_date   DATE NOT NULL,
        income_id       INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs   JSON NOT NULL DEFAULT '[]',
        created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


# ---------------------------------------------------------------------------
# DDL — local_dividend_entries
# ---------------------------------------------------------------------------
def _ddl_local_dividend_entries() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS local_dividend_entries (
            id                 SERIAL PRIMARY KEY,
            user_id            INTEGER NOT NULL
                               REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year           VARCHAR(7) NOT NULL,
            company_name       VARCHAR(128) NOT NULL,
            ex_dividend_date   DATE NOT NULL,
            wht_lkr            NUMERIC(20, 2) NOT NULL DEFAULT 0,
            income_id          INTEGER
                               REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at         TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS local_dividend_entries (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id            INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year           VARCHAR(7) NOT NULL,
        company_name       VARCHAR(128) NOT NULL,
        ex_dividend_date   DATE NOT NULL,
        wht_lkr            NUMERIC(20, 2) NOT NULL DEFAULT 0,
        income_id          INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs      JSON NOT NULL DEFAULT '[]',
        created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


_INDEXES = (
    # rental_income_entries
    ("ix_rental_income_entries_user_id",
     "rental_income_entries", "user_id"),
    ("ix_rental_income_entries_tax_year",
     "rental_income_entries", "tax_year"),
    ("ix_rental_income_entries_income_id",
     "rental_income_entries", "income_id"),
    # rental_deduction_entries
    ("ix_rental_deduction_entries_rental_id",
     "rental_deduction_entries", "rental_income_id"),
    # local_fd_interest_entries
    ("ix_local_fd_interest_user_id",
     "local_fd_interest_entries", "user_id"),
    ("ix_local_fd_interest_tax_year",
     "local_fd_interest_entries", "tax_year"),
    ("ix_local_fd_interest_income_id",
     "local_fd_interest_entries", "income_id"),
    # local_dividend_entries
    ("ix_local_dividend_user_id",
     "local_dividend_entries", "user_id"),
    ("ix_local_dividend_tax_year",
     "local_dividend_entries", "tax_year"),
    ("ix_local_dividend_income_id",
     "local_dividend_entries", "income_id"),
    # incomes.rental_income_id (the soft-link FK)
    ("ix_incomes_rental_income_id", "incomes", "rental_income_id"),
)

_COMPOSITE_INDEXES = (
    ("ix_rental_income_entries_user_tax_year",
     "rental_income_entries", ("user_id", "tax_year")),
    ("ix_rental_income_entries_user_year_addr_start",
     "rental_income_entries",
     ("user_id", "tax_year", "property_address", "period_start")),
    ("ix_rental_deduction_entries_rental_date",
     "rental_deduction_entries", ("rental_income_id", "date_incurred")),
    ("ix_local_fd_interest_user_tax_year",
     "local_fd_interest_entries", ("user_id", "tax_year")),
    ("ix_local_fd_interest_user_ref_year",
     "local_fd_interest_entries", ("user_id", "fd_account_ref", "tax_year")),
    ("ix_local_dividend_user_tax_year",
     "local_dividend_entries", ("user_id", "tax_year")),
    ("ix_local_dividend_user_date_company",
     "local_dividend_entries",
     ("user_id", "ex_dividend_date", "company_name")),
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
# ALTER incomes ADD rental_income_id (dialect-aware, idempotent)
# ---------------------------------------------------------------------------
def _add_income_rental_id() -> None:
    if _column_exists("incomes", "rental_income_id"):
        log.info("  step skip: incomes.rental_income_id already exists")
        return
    if _dialect() == "postgresql":
        sql = (
            "ALTER TABLE incomes "
            "ADD COLUMN IF NOT EXISTS rental_income_id INTEGER "
            "REFERENCES rental_income_entries(id) ON DELETE SET NULL;"
        )
    else:
        sql = (
            "ALTER TABLE incomes "
            "ADD COLUMN rental_income_id INTEGER "
            "REFERENCES rental_income_entries(id) ON DELETE SET NULL;"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: incomes.rental_income_id added")


def _verify_required_tables() -> bool:
    """Confirm M2-001 has been run before this migration activates."""
    insp = db.inspect(db.engine)
    required = ("incomes", "asset_disposals")
    missing = [t for t in required if not insp.has_table(t)]
    if missing:
        log.error(
            "MG-003-W3c cannot proceed; missing prerequisite tables: %s. "
            "Run M2-001 (20260525_130100_e_b8_schema.py upgrade) first.",
            ", ".join(missing),
        )
        return False
    log.info("MG-003-W3c prerequisites OK: %s", ", ".join(required))
    return True


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply all schema changes. Idempotent."""
    with app.app_context():
        log.info(
            "=== MG-003-W3c rental + investment_local: UPGRADE starting "
            "(dialect=%s) ===",
            _dialect(),
        )
        if not _verify_required_tables():
            log.info(
                "=== MG-003-W3c rental + investment_local: UPGRADE blocked ==="
            )
            return False

        ok = True

        # Step 1-4: create tables
        for label, ddl in (
            ("create rental_income_entries", _ddl_rental_income_entries()),
            ("create rental_deduction_entries", _ddl_rental_deduction_entries()),
            ("create local_fd_interest_entries", _ddl_local_fd_interest_entries()),
            ("create local_dividend_entries", _ddl_local_dividend_entries()),
        ):
            try:
                log.info("MG-003-W3c step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        # Step 5: ALTER incomes ADD rental_income_id
        try:
            log.info("MG-003-W3c step: add incomes.rental_income_id")
            _add_income_rental_id()
        except Exception as exc:
            db.session.rollback()
            log.error(
                "  step FAILED (incomes.rental_income_id): %s", exc,
            )
            ok = False

        # Step 6: indexes
        try:
            log.info("MG-003-W3c step: create indexes")
            _create_indexes()
            log.info("  step ok: indexes")
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (indexes): %s", exc)
            ok = False

        log.info(
            "=== MG-003-W3c rental + investment_local: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)",
        )
        return ok


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------
def downgrade() -> bool:
    """Reverse all schema changes. Only safe when reverting MS4 W3c."""
    with app.app_context():
        log.info(
            "=== MG-003-W3c rental + investment_local: DOWNGRADE starting ==="
        )
        ok = True

        drops = [
            (
                "drop incomes.rental_income_id",
                "ALTER TABLE incomes DROP COLUMN IF EXISTS rental_income_id;"
                if _dialect() == "postgresql" else
                "ALTER TABLE incomes DROP COLUMN rental_income_id;",
            ),
            ("drop rental_deduction_entries",
             "DROP TABLE IF EXISTS rental_deduction_entries;"),
            ("drop rental_income_entries",
             "DROP TABLE IF EXISTS rental_income_entries;"),
            ("drop local_dividend_entries",
             "DROP TABLE IF EXISTS local_dividend_entries;"),
            ("drop local_fd_interest_entries",
             "DROP TABLE IF EXISTS local_fd_interest_entries;"),
        ]
        for label, ddl in drops:
            try:
                log.info("MG-003-W3c step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== MG-003-W3c rental + investment_local: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL",
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
