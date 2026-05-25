"""Migration M2-002 — MS2 E.1 B8 full impl (canonical bank-parse pipeline).

NO SCHEMA CHANGES required. The MS2 E.0 schema (migration
``20260525_130100_e_b8_schema.py``) already created:

  - ``parsed_bank_statements`` (id, user_id, file_ref, parsed_at, status,
    raw_text JSON, created_at, updated_at)
  - ``incomes`` with ``bank_parse_id`` FK
  - ``remittance_entries.income_id`` FK + backfill

The B8 full-impl pipeline stores per-file metadata (original filename,
detected MIME kind, SHA-256 digest, Gemini model strategy, extraction
timestamp, raw vs validated row counts, and the validated rows
themselves) inside the existing ``raw_text`` JSON column. No new
columns are necessary.

This migration is recorded for symmetry with the M2-002 ticket; running
it is a deliberate no-op that returns 0 (success). If a future B8
iteration needs explicit columns (e.g. for indexed querying on
``mime_type``), bump M2-002 with the ALTER TABLE then.

Run::

    python migrations/20260525_130200_e_b8_impl.py upgrade
"""
from __future__ import annotations

import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M2-002")


def upgrade() -> bool:
    """No-op: B8 full impl reuses the E.0 schema. Returns True."""
    log.info("=== M2-002 B8 full impl: UPGRADE (no-op — reuses E.0 schema) ===")
    return True


def downgrade() -> bool:
    """No-op: nothing to reverse."""
    log.info("=== M2-002 B8 full impl: DOWNGRADE (no-op) ===")
    return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
