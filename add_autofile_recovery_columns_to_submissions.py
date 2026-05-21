"""Additive migration: autofile_* recovery columns on submissions.

Adds 4 columns the v1.0 Auto-File failure-mode recovery framework needs:

  - autofile_attempt_count        INTEGER NOT NULL DEFAULT 0
  - autofile_next_retry_at        TIMESTAMP NULL
  - autofile_last_attempted_at    TIMESTAMP NULL
  - autofile_last_error           VARCHAR(500) NULL

Idempotent. Safe to call on every boot. Mirrors the
`add_admin_and_stripe_columns_to_user.py` + `add_triage_answers_to_user.py`
patterns the rest of the codebase follows.

Status values written by the recovery module (not enforced as enum at
DB level — Submission.status is VARCHAR(32)):

  - autofile-pending-retry       — re-queue scheduled at autofile_next_retry_at
  - autofile-failed-needs-manual — 3 attempts exhausted; customer must
                                    manually confirm; alert dispatched
  - autofile-succeeded           — IRD accepted the submission
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def run() -> None:
    """Idempotent additive migration. Runs at app boot via main.py."""
    from app import db
    from sqlalchemy import text

    columns_to_add = [
        ("autofile_attempt_count",
         "INTEGER NOT NULL DEFAULT 0"),
        ("autofile_next_retry_at",
         "TIMESTAMP NULL"),
        ("autofile_last_attempted_at",
         "TIMESTAMP NULL"),
        ("autofile_last_error",
         "VARCHAR(500) NULL"),
    ]

    for column_name, column_spec in columns_to_add:
        try:
            # PostgreSQL ADD COLUMN IF NOT EXISTS — idempotent.
            db.session.execute(text(
                f"ALTER TABLE submissions ADD COLUMN IF NOT EXISTS "
                f"{column_name} {column_spec}"
            ))
            db.session.commit()
            log.info("submissions.%s column ensured (%s)",
                     column_name, column_spec)
        except Exception as exc:
            log.warning(
                "submissions.%s migration step failed (non-fatal — "
                "model has ORM-level column): %s",
                column_name, exc,
            )
            db.session.rollback()

    log.info("migration committed: add_autofile_recovery_columns_to_submissions")
