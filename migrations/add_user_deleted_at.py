"""
Additive schema migration — F5 GDPR/PDPA data rights (Tier D5, 2026-05-24).

Adds one nullable timestamp column to the `user` table:
  - deleted_at   TIMESTAMP   (soft-delete marker for /api/me/delete)

Soft-delete only: tax records are retained per SL Inland Revenue 6-year rule
(privacy_policy.html §4 — 7-year operational window with statutory floor of
5y). The endpoint anonymises PII (name, email) but leaves financial rows
intact so we can produce them on a Commissioner-General request.

Idempotent: ALTER TABLE is guarded by an information_schema check and a
duplicate-column catch, so it's safe to run multiple times. Same pattern as
add_triage_answers_to_user.py.

Usage:
    python migrations/add_user_deleted_at.py
"""

import logging
from sqlalchemy import text

from app import app, db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


COLUMNS = [
    ("deleted_at", "TIMESTAMP"),
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
                        log.info("user.%s already exists -- skipping", col)
                        continue
                except Exception as e:
                    log.debug(
                        "information_schema check failed (%s); attempting ALTER",
                        e,
                    )
                try:
                    conn.execute(
                        text(f'ALTER TABLE "user" ADD COLUMN {col} {sql_type}')
                    )
                    log.info("Added user.%s (%s)", col, sql_type)
                except Exception as e:
                    msg = str(e).lower()
                    if "already exists" in msg or "duplicate column" in msg:
                        log.info(
                            "user.%s already exists (caught at ALTER) -- ok", col,
                        )
                    else:
                        raise
        log.info("F5 GDPR/PDPA migration complete.")


if __name__ == "__main__":
    run()
