"""
Additive schema migration — S1 triage (Wave 1, 2026-05-20).

Adds one nullable JSON column to the `user` table:
  - triage_answers   JSON   (persists the 3 S1 fact-find answers)

Idempotent: the ALTER TABLE is guarded by an information_schema check, so it's
safe to run multiple times. Same pattern as add_tos_privacy_acceptance_to_user.py.

Storage shape (when populated):
    {
      "earning_source": "pure_foreign" | "mixed" | "pure_local",
      "earning_vehicle": ["solo_freelancer", "studio_with_subcontractors", ...],
      "filing_history": "never_filed" | "filed_manually_with_help" | ...,
      "completed_at": "2026-05-20T12:34:56Z"
    }

Usage:
    python add_triage_answers_to_user.py
"""

import logging
from sqlalchemy import text

from app import app, db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# JSON works on both PostgreSQL (native) and SQLite (stored as TEXT-with-JSON1).
# We use generic JSON; SQLAlchemy maps it to the right backend type at runtime.
COLUMNS = [
    ("triage_answers", "JSON"),
]


def _column_exists(conn, column_name: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'user' AND column_name = :col"
        ),
        {"col": column_name},
    ).fetchone()
    return row is not None


def run() -> None:
    with app.app_context():
        with db.engine.begin() as conn:
            for col, sql_type in COLUMNS:
                try:
                    if _column_exists(conn, col):
                        log.info("user.%s already exists — skipping", col)
                        continue
                except Exception as e:
                    # SQLite or other backends without information_schema fall
                    # through; ALTER TABLE will raise its own error which we
                    # swallow so test envs using SQLite still boot cleanly.
                    log.debug("information_schema check failed (%s); attempting ALTER", e)
                try:
                    conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {col} {sql_type}'))
                    log.info("Added user.%s (%s)", col, sql_type)
                except Exception as e:
                    # Idempotency safety net: if the column was added concurrently
                    # or already exists on a backend with no information_schema,
                    # the ALTER will fail with "duplicate column". That's fine.
                    msg = str(e).lower()
                    if "already exists" in msg or "duplicate column" in msg:
                        log.info("user.%s already exists (caught at ALTER) — ok", col)
                    else:
                        raise
        log.info("S1 triage migration complete.")


if __name__ == "__main__":
    run()
