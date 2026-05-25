"""
Migration M2-003 — MS2 E.1 B10 NRR classifier User columns.

Adds the three declared-facts columns the B10 NRR classifier reads/writes
on the User table:

  1. ``user.returned_to_sl_date``               DATE, nullable
  2. ``user.years_abroad_prior_to_return``      INTEGER, nullable
  3. ``user.residency_classification_log``      JSON, default '[]'

Idempotent + dialect-aware: Postgres prod (Fly/Neon) uses
``ADD COLUMN IF NOT EXISTS``; SQLite (test) introspects ``PRAGMA
table_info`` before each ALTER. Re-running is a no-op.

Run::

    python migrations/20260525_130300_e_b10_nrr.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_130300_e_b10_nrr.py upgrade'

Downgrade reverses the three columns (only use when reverting MS2 E.1)::

    python migrations/20260525_130300_e_b10_nrr.py downgrade

Provenance: B10 Inventory §B10 + Design Lock 2 §2 (extends E.0
``user.residency_status`` with the inputs the classifier needs).
"""
from __future__ import annotations

import logging
import os
import sys

# Allow running this script directly from the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app import app, db  # noqa: E402
from sqlalchemy import text  # noqa: E402

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M2-003")


# ---------------------------------------------------------------------------
# Helpers (mirror M2-001 patterns for consistency)
# ---------------------------------------------------------------------------
def _dialect() -> str:
    return db.engine.dialect.name.lower()


def _column_exists(table: str, column: str) -> bool:
    insp = db.inspect(db.engine)
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def _quote_user_table() -> str:
    return '"user"' if _dialect() == "postgresql" else "user"


# ---------------------------------------------------------------------------
# Column-adders
# ---------------------------------------------------------------------------
def _add_returned_to_sl_date() -> None:
    if _column_exists("user", "returned_to_sl_date"):
        log.info("  step skip: user.returned_to_sl_date already exists")
        return
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN IF NOT EXISTS returned_to_sl_date DATE NULL;"
        )
    else:  # sqlite
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN returned_to_sl_date DATE NULL;"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: user.returned_to_sl_date added")


def _add_years_abroad_prior_to_return() -> None:
    if _column_exists("user", "years_abroad_prior_to_return"):
        log.info("  step skip: user.years_abroad_prior_to_return already exists")
        return
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN IF NOT EXISTS years_abroad_prior_to_return INTEGER NULL;"
        )
    else:  # sqlite
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN years_abroad_prior_to_return INTEGER NULL;"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: user.years_abroad_prior_to_return added")


def _add_residency_classification_log() -> None:
    if _column_exists("user", "residency_classification_log"):
        log.info("  step skip: user.residency_classification_log already exists")
        return
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN IF NOT EXISTS residency_classification_log JSONB "
            "NOT NULL DEFAULT '[]'::jsonb;"
        )
    else:  # sqlite
        sql = (
            f"ALTER TABLE {user_tbl} "
            "ADD COLUMN residency_classification_log JSON "
            "NOT NULL DEFAULT '[]';"
        )
    db.session.execute(text(sql))
    db.session.commit()
    log.info("  step ok: user.residency_classification_log added")


# ---------------------------------------------------------------------------
# Upgrade / downgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply the three column additions. Idempotent."""
    with app.app_context():
        log.info("=== M2-003 B10 NRR: UPGRADE starting (dialect=%s) ===", _dialect())
        ok = True

        for label, fn in (
            ("add user.returned_to_sl_date", _add_returned_to_sl_date),
            ("add user.years_abroad_prior_to_return", _add_years_abroad_prior_to_return),
            ("add user.residency_classification_log", _add_residency_classification_log),
        ):
            try:
                log.info("M2-003 step: %s", label)
                fn()
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== M2-003 B10 NRR: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)",
        )
        return ok


def downgrade() -> bool:
    """Reverse the three column additions. Only safe when reverting MS2 E.1."""
    with app.app_context():
        log.info("=== M2-003 B10 NRR: DOWNGRADE starting ===")
        ok = True
        user_tbl = _quote_user_table()
        drops = [
            (
                "drop user.residency_classification_log",
                f"ALTER TABLE {user_tbl} DROP COLUMN IF EXISTS residency_classification_log;"
                if _dialect() == "postgresql"
                else f"ALTER TABLE {user_tbl} DROP COLUMN residency_classification_log;",
            ),
            (
                "drop user.years_abroad_prior_to_return",
                f"ALTER TABLE {user_tbl} DROP COLUMN IF EXISTS years_abroad_prior_to_return;"
                if _dialect() == "postgresql"
                else f"ALTER TABLE {user_tbl} DROP COLUMN years_abroad_prior_to_return;",
            ),
            (
                "drop user.returned_to_sl_date",
                f"ALTER TABLE {user_tbl} DROP COLUMN IF EXISTS returned_to_sl_date;"
                if _dialect() == "postgresql"
                else f"ALTER TABLE {user_tbl} DROP COLUMN returned_to_sl_date;",
            ),
        ]
        for label, ddl in drops:
            try:
                log.info("M2-003 step: %s", label)
                db.session.execute(text(ddl))
                db.session.commit()
                log.info("  step ok: %s", label)
            except Exception as exc:
                db.session.rollback()
                log.error("  step FAILED (%s): %s", label, exc)
                ok = False

        log.info(
            "=== M2-003 B10 NRR: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL",
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
