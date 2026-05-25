"""
Migration M2-001 — MS2 E.0 B8 schema-first / Design Lock 2.

Creates the canonical schema seam every MS2 + MS3 + MS4 income/disposal/
bank-parse/RSU code path will build on. Schema only — bank-parse logic,
NRR classification, RSU detection are downstream subagents (E.1+).

What this migration does (additive only — no column drops, no incompatible
type changes; safe to run on prod without downtime):

  1. Create table ``incomes``                — canonical income ledger (§4)
  2. Create table ``asset_disposals``        — CGT seam (§5)
  3. Create table ``parsed_bank_statements`` — B8 placeholder
  4. Create table ``rsu_vesting_events``     — B11 placeholder
  5. ALTER ``user`` ADD ``residency_status`` (str, default 'unknown')   §2
  6. ALTER ``user`` ADD ``income_sources``   (JSON, default '[]')        §3
  7. ALTER ``remittance_entries`` ADD ``income_id`` FK → ``incomes.id``  §4
  8. Backfill: for every existing RemittanceEntry, create one Income row
     with source_type='foreign_remittance' + populate the FK.

Dialect-aware: Postgres prod (Fly/Neon) uses ``ADD COLUMN IF NOT EXISTS`` +
``CREATE TABLE IF NOT EXISTS``. SQLite (test) introspects ``PRAGMA
table_info`` before each ALTER. Both paths are idempotent — re-running the
migration is a no-op on an up-to-date DB.

Run::

    python migrations/20260525_130100_e_b8_schema.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_130100_e_b8_schema.py upgrade'

Downgrade reverses tables + columns (use only when reverting MS2 E.0)::

    python migrations/20260525_130100_e_b8_schema.py downgrade

Provenance: Design Lock 2 §4-§9 (Council convergence 2026-05-25).
"""
from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal

# Allow `python migrations/20260525_130100_e_b8_schema.py upgrade` from the
# repo root by adding repo root to sys.path (matches the
# 20260525_120700_a_admin_consolidated.py pattern).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app import app, db  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M2-001")


# ---------------------------------------------------------------------------
# Dialect introspection helpers
# ---------------------------------------------------------------------------
def _dialect() -> str:
    """Return 'postgresql' | 'sqlite' | other lowercase dialect name."""
    return db.engine.dialect.name.lower()


def _table_exists(table: str) -> bool:
    """Cross-dialect table-existence check."""
    insp = db.inspect(db.engine)
    return insp.has_table(table)


def _column_exists(table: str, column: str) -> bool:
    """Cross-dialect column-existence check."""
    insp = db.inspect(db.engine)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def _quote_user_table() -> str:
    """``user`` is a reserved word in Postgres but not SQLite. Quote it."""
    return '"user"' if _dialect() == "postgresql" else "user"


# ---------------------------------------------------------------------------
# DDL — dialect-aware CREATE TABLE blocks
# ---------------------------------------------------------------------------
def _ddl_incomes() -> str:
    """Create table ``incomes`` (§4)."""
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS incomes (
            id              SERIAL PRIMARY KEY,
            user_id         INTEGER NOT NULL
                            REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year        VARCHAR(7) NOT NULL,
            source_type     VARCHAR(32) NOT NULL,
            amount          NUMERIC(20, 4) NOT NULL,
            currency        VARCHAR(3) NOT NULL DEFAULT 'LKR',
            fx_rate         NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
            fx_source       VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
            fx_date         DATE NOT NULL,
            amount_lkr      NUMERIC(20, 2) NOT NULL,
            source_country  VARCHAR(2),
            evidence_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
            remittance_id   INTEGER
                            REFERENCES remittance_entries(id) ON DELETE SET NULL,
            bank_parse_id   INTEGER,
            rsu_vesting_id  INTEGER,
            created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    # SQLite
    return """
    CREATE TABLE IF NOT EXISTS incomes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year        VARCHAR(7) NOT NULL,
        source_type     VARCHAR(32) NOT NULL,
        amount          NUMERIC(20, 4) NOT NULL,
        currency        VARCHAR(3) NOT NULL DEFAULT 'LKR',
        fx_rate         NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
        fx_source       VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
        fx_date         DATE NOT NULL,
        amount_lkr      NUMERIC(20, 2) NOT NULL,
        source_country  VARCHAR(2),
        evidence_refs   JSON NOT NULL DEFAULT '[]',
        remittance_id   INTEGER REFERENCES remittance_entries(id) ON DELETE SET NULL,
        bank_parse_id   INTEGER,
        rsu_vesting_id  INTEGER,
        created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


def _ddl_asset_disposals() -> str:
    """Create table ``asset_disposals`` (§5)."""
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS asset_disposals (
            id                  SERIAL PRIMARY KEY,
            user_id             INTEGER NOT NULL
                                REFERENCES "user"(id) ON DELETE CASCADE,
            tax_year            VARCHAR(7) NOT NULL,
            asset_type          VARCHAR(16) NOT NULL,
            acq_amount          NUMERIC(20, 4) NOT NULL,
            acq_currency        VARCHAR(3) NOT NULL DEFAULT 'LKR',
            acq_fx_rate         NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
            acq_fx_source       VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
            acq_fx_date         DATE NOT NULL,
            acq_amount_lkr      NUMERIC(20, 2) NOT NULL,
            disp_amount         NUMERIC(20, 4) NOT NULL,
            disp_currency       VARCHAR(3) NOT NULL DEFAULT 'LKR',
            disp_fx_rate        NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
            disp_fx_source      VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
            disp_fx_date        DATE NOT NULL,
            disp_amount_lkr     NUMERIC(20, 2) NOT NULL,
            gain_lkr            NUMERIC(20, 2) NOT NULL,
            acquisition_date    DATE NOT NULL,
            disposal_date       DATE NOT NULL,
            source_country      VARCHAR(2),
            asset_identifier    VARCHAR(128),
            evidence_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS asset_disposals (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id             INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        tax_year            VARCHAR(7) NOT NULL,
        asset_type          VARCHAR(16) NOT NULL,
        acq_amount          NUMERIC(20, 4) NOT NULL,
        acq_currency        VARCHAR(3) NOT NULL DEFAULT 'LKR',
        acq_fx_rate         NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
        acq_fx_source       VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
        acq_fx_date         DATE NOT NULL,
        acq_amount_lkr      NUMERIC(20, 2) NOT NULL,
        disp_amount         NUMERIC(20, 4) NOT NULL,
        disp_currency       VARCHAR(3) NOT NULL DEFAULT 'LKR',
        disp_fx_rate        NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
        disp_fx_source      VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
        disp_fx_date        DATE NOT NULL,
        disp_amount_lkr     NUMERIC(20, 2) NOT NULL,
        gain_lkr            NUMERIC(20, 2) NOT NULL,
        acquisition_date    DATE NOT NULL,
        disposal_date       DATE NOT NULL,
        source_country      VARCHAR(2),
        asset_identifier    VARCHAR(128),
        evidence_refs       JSON NOT NULL DEFAULT '[]',
        created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


def _ddl_parsed_bank_statements() -> str:
    """Create table ``parsed_bank_statements`` (B8 placeholder)."""
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS parsed_bank_statements (
            id          SERIAL PRIMARY KEY,
            user_id     INTEGER NOT NULL
                        REFERENCES "user"(id) ON DELETE CASCADE,
            file_ref    VARCHAR(512) NOT NULL,
            parsed_at   TIMESTAMP,
            status      VARCHAR(16) NOT NULL DEFAULT 'pending',
            raw_text    JSONB,
            created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS parsed_bank_statements (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        file_ref    VARCHAR(512) NOT NULL,
        parsed_at   DATETIME,
        status      VARCHAR(16) NOT NULL DEFAULT 'pending',
        raw_text    JSON,
        created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


def _ddl_rsu_vesting_events() -> str:
    """Create table ``rsu_vesting_events`` (B11 placeholder)."""
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS rsu_vesting_events (
            id                       SERIAL PRIMARY KEY,
            user_id                  INTEGER NOT NULL
                                     REFERENCES "user"(id) ON DELETE CASCADE,
            vesting_date             DATE NOT NULL,
            fair_market_value_money  JSONB NOT NULL,
            ticker                   VARCHAR(16),
            source_country           VARCHAR(2),
            created_at               TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at               TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS rsu_vesting_events (
        id                       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                  INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        vesting_date             DATE NOT NULL,
        fair_market_value_money  JSON NOT NULL,
        ticker                   VARCHAR(16),
        source_country           VARCHAR(2),
        created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


# ---------------------------------------------------------------------------
# DDL — index helpers
# ---------------------------------------------------------------------------
_INDEXES = (
    ("ix_incomes_user_id", "incomes", "user_id"),
    ("ix_incomes_tax_year", "incomes", "tax_year"),
    ("ix_incomes_amount_lkr", "incomes", "amount_lkr"),
    ("ix_incomes_remittance_id", "incomes", "remittance_id"),
    ("ix_incomes_bank_parse_id", "incomes", "bank_parse_id"),
    ("ix_incomes_rsu_vesting_id", "incomes", "rsu_vesting_id"),
    ("ix_asset_disposals_user_id", "asset_disposals", "user_id"),
    ("ix_asset_disposals_tax_year", "asset_disposals", "tax_year"),
    ("ix_parsed_bank_statements_user_id", "parsed_bank_statements", "user_id"),
    ("ix_rsu_vesting_events_user_id", "rsu_vesting_events", "user_id"),
)

_COMPOSITE_INDEXES = (
    ("ix_incomes_user_tax_year", "incomes", ("user_id", "tax_year")),
    ("ix_incomes_source_type_tax_year", "incomes", ("source_type", "tax_year")),
    ("ix_asset_disposals_user_tax_year", "asset_disposals", ("user_id", "tax_year")),
)


def _create_indexes() -> None:
    """Create all indexes. ``CREATE INDEX IF NOT EXISTS`` works on both
    Postgres and SQLite, so we can use a single statement."""
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
# ALTER TABLE helpers (dialect-aware, idempotent)
# ---------------------------------------------------------------------------
def _add_user_residency_status() -> None:
    if _column_exists("user", "residency_status"):
        log.info("  step skip: user.residency_status already exists")
        return
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN IF NOT EXISTS residency_status VARCHAR(16) "
            "NOT NULL DEFAULT 'unknown';"
        )
    else:  # sqlite
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN residency_status VARCHAR(16) "
            "NOT NULL DEFAULT 'unknown';"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: user.residency_status added")


def _add_user_income_sources() -> None:
    if _column_exists("user", "income_sources"):
        log.info("  step skip: user.income_sources already exists")
        return
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN IF NOT EXISTS income_sources JSONB "
            "NOT NULL DEFAULT '[]'::jsonb;"
        )
    else:  # sqlite
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN income_sources JSON "
            "NOT NULL DEFAULT '[]';"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: user.income_sources added")


def _add_remittance_income_id() -> None:
    if _column_exists("remittance_entries", "income_id"):
        log.info("  step skip: remittance_entries.income_id already exists")
        return
    if _dialect() == "postgresql":
        sql = (
            "ALTER TABLE remittance_entries "
            "ADD COLUMN IF NOT EXISTS income_id INTEGER "
            "REFERENCES incomes(id) ON DELETE SET NULL;"
        )
    else:  # sqlite — FK constraints in ADD COLUMN are accepted but not
        # enforced unless PRAGMA foreign_keys=ON. We add the column
        # regardless so the ORM-level FK works.
        sql = (
            "ALTER TABLE remittance_entries "
            "ADD COLUMN income_id INTEGER "
            "REFERENCES incomes(id) ON DELETE SET NULL;"
        )
    db.session.execute(text(sql))
    db.session.commit()
    db.session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_remittance_entries_income_id "
            "ON remittance_entries (income_id);"
        )
    )
    db.session.commit()
    log.info("  step ok: remittance_entries.income_id added + indexed")


# ---------------------------------------------------------------------------
# Backfill — RemittanceEntry → Income (one row per entry; idempotent)
# ---------------------------------------------------------------------------
def _tax_year_for_remittance(remittance_date) -> str:
    """SL Y/A runs 1 April → 31 March. Returns 'YYYY/YY' e.g. '2025/26'.

    The Income.tax_year column uses VARCHAR(7) shape ``YYYY/YY`` per
    Design Lock 2 §4. RemittanceEntry already stores ``YYYY-YY`` shape;
    we re-derive from the remittance_date for consistency with §4 wording.
    """
    d = remittance_date
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}/{str(start + 1)[2:]}"


def _backfill_remittance_to_income() -> int:
    """For every RemittanceEntry with income_id IS NULL, create one Income
    row with source_type='foreign_remittance' and populate the FK.

    Idempotent: only processes entries where income_id IS NULL. Re-running
    after a partial migration is safe.

    Returns the number of rows backfilled.
    """
    # Lazy imports — model classes registered only after app context is up
    from remittance_models import RemittanceEntry  # noqa: WPS433
    from fiesta.tax.models import Income  # noqa: WPS433

    todo = (
        RemittanceEntry.query
        .filter(RemittanceEntry.income_id.is_(None))
        .all()
    )

    backfilled = 0
    for r in todo:
        # Prefer CBSL rate; fall back to bank rate when CBSL missing.
        if r.lkr_amount_cbsl is not None:
            amount_lkr = Decimal(r.lkr_amount_cbsl)
            fx_source = "CBSL"
        elif r.lkr_amount_bank_rate is not None:
            amount_lkr = Decimal(r.lkr_amount_bank_rate)
            fx_source = "bank_statement"
        else:
            # Cannot derive LKR — skip (will be re-tried after rate captured).
            continue

        fx_rate = (
            (amount_lkr / Decimal(r.foreign_amount))
            if r.foreign_amount and Decimal(r.foreign_amount) != 0
            else Decimal("1.0")
        )

        income = Income(
            user_id=r.user_id,
            tax_year=_tax_year_for_remittance(r.remittance_date),
            source_type="foreign_remittance",
            amount=Decimal(r.foreign_amount),
            currency=r.foreign_currency,
            fx_rate=fx_rate,
            fx_source=fx_source,
            fx_date=r.remittance_date,
            amount_lkr=amount_lkr,
            source_country=r.source_country,
            evidence_refs=[
                {"type": "remittance_entry", "ref_id": int(r.id)}
            ],
            remittance_id=r.id,
        )
        db.session.add(income)
        # Flush so income.id is populated before we set the back-pointer.
        db.session.flush()
        r.income_id = income.id
        backfilled += 1

    db.session.commit()
    log.info("  step ok: backfilled %d RemittanceEntry → Income rows", backfilled)
    return backfilled


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply all schema changes + backfill. Idempotent."""
    with app.app_context():
        log.info("=== M2-001 B8 schema-first: UPGRADE starting (dialect=%s) ===", _dialect())
        ok = True

        # Step 1-4: create tables
        for label, ddl in (
            ("create incomes", _ddl_incomes()),
            ("create asset_disposals", _ddl_asset_disposals()),
            ("create parsed_bank_statements", _ddl_parsed_bank_statements()),
            ("create rsu_vesting_events", _ddl_rsu_vesting_events()),
        ):
            try:
                log.info("M2-001 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        # Indexes
        try:
            log.info("M2-001 step: create indexes")
            _create_indexes()
            log.info("  step ok: indexes")
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (indexes): %s", exc)
            ok = False

        # Step 5: user.residency_status
        try:
            log.info("M2-001 step: add user.residency_status")
            _add_user_residency_status()
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (user.residency_status): %s", exc)
            ok = False

        # Step 6: user.income_sources
        try:
            log.info("M2-001 step: add user.income_sources")
            _add_user_income_sources()
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (user.income_sources): %s", exc)
            ok = False

        # Step 7: remittance_entries.income_id
        try:
            log.info("M2-001 step: add remittance_entries.income_id")
            _add_remittance_income_id()
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (remittance_entries.income_id): %s", exc)
            ok = False

        # Step 8: backfill (only if remittance_entries table exists)
        try:
            if _table_exists("remittance_entries"):
                log.info("M2-001 step: backfill RemittanceEntry → Income")
                _backfill_remittance_to_income()
            else:
                log.info("  step skip: remittance_entries table not present")
        except Exception as exc:
            db.session.rollback()
            log.error("  step FAILED (backfill): %s", exc)
            ok = False

        log.info(
            "=== M2-001 B8 schema-first: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)"
        )
        return ok


# ---------------------------------------------------------------------------
# Downgrade (only safe before any non-migrated code starts writing here)
# ---------------------------------------------------------------------------
def downgrade() -> bool:
    """Reverse all schema changes. Only safe when reverting MS2 E.0."""
    with app.app_context():
        log.info("=== M2-001 B8 schema-first: DOWNGRADE starting ===")
        ok = True

        user_tbl = _quote_user_table()

        # Reverse in roughly reverse order. Postgres supports DROP COLUMN
        # IF EXISTS; SQLite supports DROP COLUMN since 3.35.
        drops = [
            ("drop remittance_entries.income_id",
             "ALTER TABLE remittance_entries DROP COLUMN IF EXISTS income_id;"
             if _dialect() == "postgresql" else
             "ALTER TABLE remittance_entries DROP COLUMN income_id;"),
            ("drop user.income_sources",
             f"ALTER TABLE {user_tbl} DROP COLUMN IF EXISTS income_sources;"
             if _dialect() == "postgresql" else
             f"ALTER TABLE {user_tbl} DROP COLUMN income_sources;"),
            ("drop user.residency_status",
             f"ALTER TABLE {user_tbl} DROP COLUMN IF EXISTS residency_status;"
             if _dialect() == "postgresql" else
             f"ALTER TABLE {user_tbl} DROP COLUMN residency_status;"),
            ("drop rsu_vesting_events", "DROP TABLE IF EXISTS rsu_vesting_events;"),
            ("drop parsed_bank_statements", "DROP TABLE IF EXISTS parsed_bank_statements;"),
            ("drop asset_disposals", "DROP TABLE IF EXISTS asset_disposals;"),
            ("drop incomes", "DROP TABLE IF EXISTS incomes;"),
        ]
        for label, ddl in drops:
            try:
                log.info("M2-001 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== M2-001 B8 schema-first: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL"
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
