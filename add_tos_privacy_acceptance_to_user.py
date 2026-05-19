"""
Additive schema migration — S2 signup (Wave 1, 2026-05-20).

Adds four nullable columns to the `user` table:
  - tos_accepted_version       VARCHAR(32)
  - tos_accepted_at            TIMESTAMP
  - privacy_accepted_version   VARCHAR(32)
  - privacy_accepted_at        TIMESTAMP

Idempotent: each ALTER TABLE is guarded by an information_schema check, so it's
safe to run multiple times (matches the additive-migration pattern of
`add_persona_and_remittance.py` and friends in this repo).

Usage:
    python add_tos_privacy_acceptance_to_user.py
"""
import logging
from sqlalchemy import text

from app import app, db

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


COLUMNS = [
    ("tos_accepted_version", "VARCHAR(32)"),
    ("tos_accepted_at", "TIMESTAMP"),
    ("privacy_accepted_version", "VARCHAR(32)"),
    ("privacy_accepted_at", "TIMESTAMP"),
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
                if _column_exists(conn, col):
                    log.info("user.%s already exists — skipping", col)
                    continue
                conn.execute(text(f'ALTER TABLE "user" ADD COLUMN {col} {sql_type}'))
                log.info("Added user.%s (%s)", col, sql_type)
        log.info("S2 signup migration complete.")


if __name__ == "__main__":
    run()
