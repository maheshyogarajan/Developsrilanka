"""
Migration M2-004 — MS2 E.1 B11 RSU classifier.

Tables: NO new tables. RSUVestingEvent + AssetDisposal + Income were all
created by M2-001 (20260525_130100_e_b8_schema.py) per Design Lock 2 §5
(asset_type='rsu' on AssetDisposal) and §4 (source_type='rsu' on Income).

The B11 classifier (fiesta.tax.rsu_engine) uses these existing tables
exclusively; no schema additions are required.

This migration file exists for ledger completeness so the migration
sequence reads M2-001 → M2-002 (B8 full impl) → M2-003 (NRR) → M2-004 (B11)
and so a later phase that DOES need RSU-specific metadata (vesting_schedule,
grant_date, employer_entity, vesting_tranche) has a clean upgrade slot to
insert ALTER TABLE statements without re-numbering.

For now: upgrade() is a no-op that logs the activation event so the
deployment audit trail records "B11 RSU classifier active as of <date>".

Run::

    python migrations/20260525_130400_e_b11_rsu.py upgrade

Production (Fly)::

    flyctl ssh console -a fiesta-mvp -C \\
      'python migrations/20260525_130400_e_b11_rsu.py upgrade'

Provenance: Inventory §B11 + Design Lock 2 §5/§6.
"""
from __future__ import annotations

import logging
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M2-004")


def _verify_required_tables() -> bool:
    """Confirm M2-001 has been run before B11 activates."""
    from app import app, db
    with app.app_context():
        insp = db.inspect(db.engine)
        required = ("incomes", "asset_disposals", "rsu_vesting_events")
        missing = [t for t in required if not insp.has_table(t)]
        if missing:
            log.error(
                "M2-004 cannot proceed; missing prerequisite tables: %s. "
                "Run M2-001 (20260525_130100_e_b8_schema.py upgrade) first.",
                ", ".join(missing),
            )
            return False
        log.info("M2-004 prerequisites OK: %s", ", ".join(required))
        return True


def upgrade() -> bool:
    """No-op activation marker for B11 RSU classifier."""
    log.info("=== M2-004 B11 RSU classifier: UPGRADE starting ===")
    if not _verify_required_tables():
        log.info("=== M2-004 B11 RSU classifier: UPGRADE blocked ===")
        return False
    log.info("M2-004 step: schema unchanged (RSUVestingEvent + AssetDisposal + Income exist)")
    log.info("M2-004 step: engine module fiesta.tax.rsu_engine active")
    log.info("M2-004 step: routes fiesta.rsu.routes mounted at /income/rsu")
    log.info("=== M2-004 B11 RSU classifier: UPGRADE complete ===")
    return True


def downgrade() -> bool:
    """No-op — there is no schema change to reverse."""
    log.info("=== M2-004 B11 RSU classifier: DOWNGRADE no-op ===")
    return True


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
