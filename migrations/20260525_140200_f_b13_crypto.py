"""
Migration M3-002 — MS3 B13 Crypto / Capital Gains Tax classifier.

Tables created:
  - ``crypto_positions`` — open crypto acquisition lots (FIFO matching base)

Tables referenced (created by earlier migrations):
  - ``asset_disposals``  (M2-001 — Design Lock 2 §5; reused with
                         ``asset_type='crypto'`` per §8 anti-pattern lock —
                         no separate CryptoDisposal table)
  - ``incomes``           (M2-001 — not written by B13 v1.0; crypto income
                          on staking/yield deferred to a future iteration)
  - ``"user"``            (existing — FK target)

Dialect-aware: Postgres prod, SQLite test.
Idempotent: ``CREATE TABLE IF NOT EXISTS`` everywhere.
Auto-applied on boot via main.py (added below the M2-004 RSU loader).

Provenance: Inventory §B13, Design Lock 2 §5/§8, IRA Sections 7(2)(b), 36, 37.
"""
from __future__ import annotations

import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M3-002")


def _dialect() -> str:
    from app import db
    try:
        return db.engine.dialect.name
    except Exception:
        return os.environ.get("DB_DIALECT", "postgresql").lower()


def _ddl_crypto_positions() -> str:
    """Create table ``crypto_positions`` (B13)."""
    if _dialect() == "postgresql":
        return """
        CREATE TABLE IF NOT EXISTS crypto_positions (
            id                          SERIAL PRIMARY KEY,
            user_id                     INTEGER NOT NULL
                                        REFERENCES "user"(id) ON DELETE CASCADE,
            asset_identifier            VARCHAR(16) NOT NULL,
            acquisition_date            DATE NOT NULL,
            shares                      NUMERIC(28, 12) NOT NULL,
            shares_remaining            NUMERIC(28, 12) NOT NULL,
            acq_amount                  NUMERIC(20, 4) NOT NULL,
            acq_currency                VARCHAR(3) NOT NULL DEFAULT 'LKR',
            acq_fx_rate                 NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
            acq_fx_source               VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
            acq_fx_date                 DATE NOT NULL,
            acq_amount_lkr              NUMERIC(20, 2) NOT NULL,
            acq_amount_lkr_per_share    NUMERIC(28, 8) NOT NULL,
            source_country              VARCHAR(2),
            evidence_refs               JSONB NOT NULL DEFAULT '[]'::jsonb,
            closed_at                   TIMESTAMP,
            created_at                  TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """
    return """
    CREATE TABLE IF NOT EXISTS crypto_positions (
        id                          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id                     INTEGER NOT NULL
                                    REFERENCES user(id) ON DELETE CASCADE,
        asset_identifier            VARCHAR(16) NOT NULL,
        acquisition_date            DATE NOT NULL,
        shares                      NUMERIC(28, 12) NOT NULL,
        shares_remaining            NUMERIC(28, 12) NOT NULL,
        acq_amount                  NUMERIC(20, 4) NOT NULL,
        acq_currency                VARCHAR(3) NOT NULL DEFAULT 'LKR',
        acq_fx_rate                 NUMERIC(20, 8) NOT NULL DEFAULT 1.0,
        acq_fx_source               VARCHAR(32) NOT NULL DEFAULT 'lkr_native',
        acq_fx_date                 DATE NOT NULL,
        acq_amount_lkr              NUMERIC(20, 2) NOT NULL,
        acq_amount_lkr_per_share    NUMERIC(28, 8) NOT NULL,
        source_country              VARCHAR(2),
        evidence_refs               JSON NOT NULL DEFAULT '[]',
        closed_at                   DATETIME,
        created_at                  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at                  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """


_INDEXES = (
    ("ix_crypto_positions_user_id", "crypto_positions", "user_id"),
    ("ix_crypto_positions_asset_identifier", "crypto_positions", "asset_identifier"),
)

_COMPOSITE_INDEXES = (
    (
        "ix_crypto_positions_user_asset_date",
        "crypto_positions",
        ("user_id", "asset_identifier", "acquisition_date"),
    ),
    (
        "ix_crypto_positions_user_asset_open",
        "crypto_positions",
        ("user_id", "asset_identifier", "closed_at"),
    ),
)


def _verify_required_tables() -> bool:
    """Confirm prerequisite tables exist (M2-001 must have been run)."""
    from app import app, db
    with app.app_context():
        insp = db.inspect(db.engine)
        required = ("asset_disposals", "incomes")
        missing = [t for t in required if not insp.has_table(t)]
        if missing:
            log.error(
                "M3-002 cannot proceed; missing prerequisite tables: %s. "
                "Run M2-001 (20260525_130100_e_b8_schema.py upgrade) first.",
                ", ".join(missing),
            )
            return False
        log.info("M3-002 prerequisites OK: %s", ", ".join(required))
        return True


def upgrade() -> bool:
    """Create crypto_positions table + indexes. Idempotent."""
    from sqlalchemy import text as _sql_text
    from app import app, db

    log.info("=== M3-002 B13 Crypto/CGT classifier: UPGRADE starting ===")
    if not _verify_required_tables():
        log.info("=== M3-002 B13 Crypto/CGT classifier: UPGRADE blocked ===")
        return False

    with app.app_context():
        try:
            db.session.execute(_sql_text(_ddl_crypto_positions()))
            log.info("M3-002 step: crypto_positions table ensured")

            for ix_name, table, col in _INDEXES:
                stmt = (
                    f"CREATE INDEX IF NOT EXISTS {ix_name} ON {table} ({col})"
                )
                db.session.execute(_sql_text(stmt))
                log.info("M3-002 step: index %s ensured on %s(%s)", ix_name, table, col)

            for ix_name, table, cols in _COMPOSITE_INDEXES:
                col_list = ", ".join(cols)
                stmt = (
                    f"CREATE INDEX IF NOT EXISTS {ix_name} ON {table} ({col_list})"
                )
                db.session.execute(_sql_text(stmt))
                log.info("M3-002 step: composite index %s ensured", ix_name)

            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            log.exception("M3-002 failed during DDL: %s", exc)
            return False

    log.info("M3-002 step: engine module fiesta.tax.crypto_cgt active")
    log.info("M3-002 step: routes fiesta.crypto.routes mounted at /income/crypto")
    log.info("=== M3-002 B13 Crypto/CGT classifier: UPGRADE complete ===")
    return True


def downgrade() -> bool:
    """Drop the crypto_positions table. AssetDisposal rows with
    asset_type='crypto' are left intact (downstream history).
    """
    from sqlalchemy import text as _sql_text
    from app import app, db

    log.info("=== M3-002 B13 Crypto/CGT classifier: DOWNGRADE starting ===")
    with app.app_context():
        try:
            for ix_name, _table, _col in _INDEXES:
                db.session.execute(_sql_text(f"DROP INDEX IF EXISTS {ix_name}"))
            for ix_name, _table, _cols in _COMPOSITE_INDEXES:
                db.session.execute(_sql_text(f"DROP INDEX IF EXISTS {ix_name}"))
            db.session.execute(_sql_text("DROP TABLE IF EXISTS crypto_positions"))
            db.session.commit()
            log.info("M3-002 step: crypto_positions + indexes dropped")
        except Exception as exc:
            db.session.rollback()
            log.exception("M3-002 downgrade failed: %s", exc)
            return False

    log.info("=== M3-002 B13 Crypto/CGT classifier: DOWNGRADE complete ===")
    return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
