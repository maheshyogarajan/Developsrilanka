"""
Migration MARKOV-L2-001 — Markov Layer 2 event-driven state history.

Creates ``user_state_history`` — the append-only time-series log of every
Markov state transition. Layer 1 (/admin/fiesta-states) derives current
state on demand; Layer 2 is the time series we need to compute dwell
time, conversion rate by cohort, and funnel velocity.

What this migration does
========================
  1. Create table ``user_state_history`` (idempotent CREATE IF NOT EXISTS).
  2. Create indexes:
        - ix_user_state_history_user_id        (single-col, FK lookup)
        - ix_user_state_history_created_at     (chronological queries)
        - ix_user_state_history_user_created   (composite, dwell time)

NOT included in this migration (intentional, separate concern):
  * Backfill of existing 3,877 users. The backfill is a manual one-shot
    runnable as ``flask markov backfill --commit`` so the orchestrator
    can sequence it relative to traffic.

Dialect-aware: Postgres prod (Fly/Neon) uses SERIAL/BIGSERIAL +
``CREATE TABLE IF NOT EXISTS``. SQLite (test) uses INTEGER PRIMARY KEY +
``CREATE TABLE IF NOT EXISTS``. Both paths are idempotent — re-running
the migration is a no-op on an up-to-date DB.

Run::

    python migrations/20260527_100100_markov_l2_user_state_history.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260527_100100_markov_l2_user_state_history.py upgrade'

Downgrade DROPs the table (use only when reverting Markov-L2)::

    python migrations/20260527_100100_markov_l2_user_state_history.py downgrade

Provenance: post-launch Day-1 task #11, 2026-05-27.
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
log = logging.getLogger("MARKOV-L2-001")


# ---------------------------------------------------------------------------
# Dialect introspection helpers
# ---------------------------------------------------------------------------
def _dialect() -> str:
    return db.engine.dialect.name.lower()


def _quote_user_table() -> str:
    return '"user"' if _dialect() == "postgresql" else "user"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
def _ddl_user_state_history() -> str:
    """Return the dialect-appropriate ``CREATE TABLE`` for user_state_history."""
    user_tbl = _quote_user_table()
    if _dialect() == "postgresql":
        return f"""
        CREATE TABLE IF NOT EXISTS user_state_history (
            id                   BIGSERIAL PRIMARY KEY,
            user_id              INTEGER NOT NULL
                                 REFERENCES {user_tbl}(id),
            state_code           VARCHAR(8)  NOT NULL,
            state_label          VARCHAR(64) NOT NULL,
            previous_state_code  VARCHAR(8),
            trigger_event        VARCHAR(64) NOT NULL,
            metadata_json        JSON,
            created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    # SQLite path — INTEGER PRIMARY KEY autoincrements; JSON stored as text.
    return f"""
    CREATE TABLE IF NOT EXISTS user_state_history (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id              INTEGER NOT NULL
                             REFERENCES {user_tbl}(id),
        state_code           VARCHAR(8)  NOT NULL,
        state_label          VARCHAR(64) NOT NULL,
        previous_state_code  VARCHAR(8),
        trigger_event        VARCHAR(64) NOT NULL,
        metadata_json        TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """


_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS ix_user_state_history_user_id "
    "ON user_state_history (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_user_state_history_created_at "
    "ON user_state_history (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_user_state_history_user_created "
    "ON user_state_history (user_id, created_at)",
]


# ---------------------------------------------------------------------------
# Upgrade / downgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Create user_state_history + indexes. Idempotent."""
    with app.app_context():
        log.info("=== MARKOV-L2-001: UPGRADE starting (dialect=%s) ===", _dialect())
        try:
            db.session.execute(text(_ddl_user_state_history()))
            for stmt in _INDEX_STATEMENTS:
                db.session.execute(text(stmt))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            log.error("MARKOV-L2-001 UPGRADE failed: %s", exc)
            return False
        log.info("=== MARKOV-L2-001: UPGRADE complete ===")
        return True


def downgrade() -> bool:
    """Drop user_state_history. Use ONLY when reverting Markov-L2."""
    with app.app_context():
        log.info("=== MARKOV-L2-001: DOWNGRADE starting ===")
        try:
            db.session.execute(text("DROP TABLE IF EXISTS user_state_history"))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            log.error("MARKOV-L2-001 DOWNGRADE failed: %s", exc)
            return False
        log.info("=== MARKOV-L2-001: DOWNGRADE complete ===")
        return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
