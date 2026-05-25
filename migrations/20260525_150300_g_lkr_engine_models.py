"""
Migration MG-003 — MS4 W3b G3.1 + G3.2 LKR engine metadata tables.

Creates the per-employer + per-invoice metadata tables that pair with
canonical Income rows for the LKR engine extensions (G3.1 employment +
G3.2 professional fees).

What this migration does (additive only — no column drops, no incompatible
type changes; safe to run on prod without downtime):

  1. Create table ``employment_income_metadata`` — one row per
     (user, employer_name, period_start). 1:1 with an Income row that
     holds the gross-employment Money. Carries APIT credit (LKR), APIT
     certificate reference, the employment period window, evidence refs.
  2. Create table ``professional_fee_metadata`` — one row per
     (user, client_name, invoice_date). 1:1 with an Income row that
     holds the gross-invoice Money. Carries §85 WHT credit (LKR),
     invoice_number, WHT certificate reference, service_description.
  3. Create indexes on both tables — user_id, tax_year, and the natural-
     key composite indexes used by the idempotency finders.

Dialect-aware: Postgres prod (Fly/Neon) uses ``CREATE TABLE IF NOT EXISTS``;
SQLite (test) uses the same statements without the JSONB-vs-JSON distinction.
Both paths are idempotent.

Run::

    python migrations/20260525_150300_g_lkr_engine_models.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_150300_g_lkr_engine_models.py upgrade'

Downgrade reverses tables (use only when reverting MS4 W3b)::

    python migrations/20260525_150300_g_lkr_engine_models.py downgrade

Provenance: Section G G3.1/G3.2 +
working files/_fiesta_unification_addendum_20260525.md +
Design Lock 2 §1/§4 (canonical Money + Income, ORM models pair via
income_id FK on the metadata side).
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
log = logging.getLogger("MG-003")


# ---------------------------------------------------------------------------
# Dialect introspection helpers
# ---------------------------------------------------------------------------
def _dialect() -> str:
    return db.engine.dialect.name.lower()


def _table_exists(table: str) -> bool:
    insp = db.inspect(db.engine)
    return insp.has_table(table)


# ---------------------------------------------------------------------------
# DDL — employment_income_metadata
# ---------------------------------------------------------------------------
def _ddl_employment_income_metadata() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS employment_income_metadata (
            id                      SERIAL PRIMARY KEY,
            user_id                 INTEGER NOT NULL
                                    REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year                VARCHAR(7) NOT NULL,
            employer_name           VARCHAR(128) NOT NULL,
            apit_certificate_ref    VARCHAR(128),
            apit_credit_lkr         NUMERIC(20, 2) NOT NULL DEFAULT 0,
            period_start            DATE NOT NULL,
            period_end              DATE NOT NULL,
            income_id               INTEGER
                                    REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs           JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    # SQLite
    return """
    CREATE TABLE IF NOT EXISTS employment_income_metadata (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                 INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year                VARCHAR(7) NOT NULL,
        employer_name           VARCHAR(128) NOT NULL,
        apit_certificate_ref    VARCHAR(128),
        apit_credit_lkr         NUMERIC(20, 2) NOT NULL DEFAULT 0,
        period_start            DATE NOT NULL,
        period_end              DATE NOT NULL,
        income_id               INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs           JSON NOT NULL DEFAULT '[]',
        created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


# ---------------------------------------------------------------------------
# DDL — professional_fee_metadata
# ---------------------------------------------------------------------------
def _ddl_professional_fee_metadata() -> str:
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS professional_fee_metadata (
            id                      SERIAL PRIMARY KEY,
            user_id                 INTEGER NOT NULL
                                    REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year                VARCHAR(7) NOT NULL,
            client_name             VARCHAR(128) NOT NULL,
            invoice_number          VARCHAR(64),
            wht_certificate_ref     VARCHAR(128),
            wht_credit_lkr          NUMERIC(20, 2) NOT NULL DEFAULT 0,
            invoice_date            DATE NOT NULL,
            service_description     VARCHAR(512),
            income_id               INTEGER
                                    REFERENCES incomes(id) ON DELETE SET NULL,
            evidence_refs           JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at              TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS professional_fee_metadata (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                 INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year                VARCHAR(7) NOT NULL,
        client_name             VARCHAR(128) NOT NULL,
        invoice_number          VARCHAR(64),
        wht_certificate_ref     VARCHAR(128),
        wht_credit_lkr          NUMERIC(20, 2) NOT NULL DEFAULT 0,
        invoice_date            DATE NOT NULL,
        service_description     VARCHAR(512),
        income_id               INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
        evidence_refs           JSON NOT NULL DEFAULT '[]',
        created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


_INDEXES = (
    ("ix_employment_income_metadata_user_id",
     "employment_income_metadata", "user_id"),
    ("ix_employment_income_metadata_tax_year",
     "employment_income_metadata", "tax_year"),
    ("ix_employment_income_metadata_income_id",
     "employment_income_metadata", "income_id"),
    ("ix_professional_fee_metadata_user_id",
     "professional_fee_metadata", "user_id"),
    ("ix_professional_fee_metadata_tax_year",
     "professional_fee_metadata", "tax_year"),
    ("ix_professional_fee_metadata_income_id",
     "professional_fee_metadata", "income_id"),
)

_COMPOSITE_INDEXES = (
    # Idempotency anchors
    ("ix_employment_income_metadata_user_tax_year",
     "employment_income_metadata", ("user_id", "tax_year")),
    ("ix_employment_income_metadata_user_emp_period",
     "employment_income_metadata", ("user_id", "employer_name", "period_start")),
    ("ix_professional_fee_metadata_user_tax_year",
     "professional_fee_metadata", ("user_id", "tax_year")),
    ("ix_professional_fee_metadata_user_client_date",
     "professional_fee_metadata", ("user_id", "client_name", "invoice_date")),
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


def _verify_required_tables() -> bool:
    """Confirm M2-001 has been run before MG-003 activates."""
    insp = db.inspect(db.engine)
    if not insp.has_table("incomes"):
        log.error(
            "MG-003 cannot proceed; missing prerequisite table 'incomes'. "
            "Run M2-001 (20260525_130100_e_b8_schema.py upgrade) first."
        )
        return False
    log.info("MG-003 prerequisites OK: incomes present")
    return True


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply all schema changes. Idempotent."""
    with app.app_context():
        log.info(
            "=== MG-003 G3 LKR engine metadata: UPGRADE starting (dialect=%s) ===",
            _dialect(),
        )
        if not _verify_required_tables():
            log.info("=== MG-003: UPGRADE blocked ===")
            return False

        ok = True

        # Step 1-2: create tables
        for label, ddl in (
            ("create employment_income_metadata",
             _ddl_employment_income_metadata()),
            ("create professional_fee_metadata",
             _ddl_professional_fee_metadata()),
        ):
            try:
                log.info("MG-003 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        # Step 3: indexes
        try:
            log.info("MG-003 step: create indexes")
            _create_indexes()
            log.info("  step ok: indexes")
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (indexes): %s", exc)
            ok = False

        log.info(
            "=== MG-003 G3 LKR engine metadata: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)",
        )
        return ok


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------
def downgrade() -> bool:
    """Reverse all schema changes. Only safe when reverting MS4 W3b."""
    with app.app_context():
        log.info("=== MG-003 G3 LKR engine metadata: DOWNGRADE starting ===")
        ok = True

        drops = [
            ("drop professional_fee_metadata",
             "DROP TABLE IF EXISTS professional_fee_metadata;"),
            ("drop employment_income_metadata",
             "DROP TABLE IF EXISTS employment_income_metadata;"),
        ]
        for label, ddl in drops:
            try:
                log.info("MG-003 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== MG-003 G3 LKR engine metadata: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL",
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
