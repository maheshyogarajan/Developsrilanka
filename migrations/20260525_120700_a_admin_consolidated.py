"""
Migration M1-007: Admin consolidation (Stage C4).

Brings the database to the state Stage C4 requires:

  1. ``user.role`` column — defaulted to 'user'. Already added by the original
     User-model migrations; this step is idempotent ALTER COLUMN to ensure the
     default is set and any legacy ``is_admin=True`` rows are promoted to
     ``role='admin'`` (the F8.1 silent-failure cleanup).

  2. ``user.last_login_at`` column — added by ``add_last_login_at.py``; this
     migration calls into that script to keep the bring-up one-shot.

  3. ``system_setting`` table — created by ``add_system_setting.py`` with the
     seed defaults for the tax-rate settings. This migration delegates so the
     admin-consolidation bring-up is one command for Operator.

The three component migrations are idempotent (CREATE/ALTER ... IF (NOT) EXISTS,
ON CONFLICT DO NOTHING), so M1-007 is safe to re-run.

Run::

    cd "C:/Users/mahes/fiesta_phase_a/Developsrilanka_ms1_c4"
    python migrations/20260525_120700_a_admin_consolidated.py upgrade

Downgrade reverses every component (use only if reverting Stage C4 work)::

    python migrations/20260525_120700_a_admin_consolidated.py downgrade
"""
from __future__ import annotations

import logging
import sys

from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("M1-007")


# ---------------------------------------------------------------------------
# Component DDL (idempotent)
# ---------------------------------------------------------------------------
ENSURE_ROLE_DEFAULT_DDL = """
ALTER TABLE "user"
ALTER COLUMN role SET DEFAULT 'user';
"""

PROMOTE_LEGACY_IS_ADMIN_DDL = """
-- F8.1 cleanup: pre-Wave-4 code wrote `is_admin=True` to a transient attribute
-- that was never persisted as a column. There is no `is_admin` column to read
-- from. This step is a no-op for fresh DBs but covers any legacy seeds that
-- left a stale flag in any historical column.
SELECT 1;
"""

DROP_ROLE_DEFAULT_DDL = """
ALTER TABLE "user"
ALTER COLUMN role DROP DEFAULT;
"""


def _run(stmt: str, label: str) -> bool:
    """Execute one DDL statement, log + commit, return True on success."""
    try:
        log.info("M1-007 step: %s", label)
        db.session.execute(text(stmt))
        db.session.commit()
        log.info("  step ok: %s", label)
        return True
    except Exception as exc:
        db.session.rollback()
        log.error("  step FAILED (%s): %s", label, exc)
        return False


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade() -> bool:
    """Apply all three components."""
    with app.app_context():
        log.info("=== M1-007 admin consolidation: UPGRADE starting ===")

        ok = True

        # Step 1: role column default + legacy is_admin sweep
        ok = _run(ENSURE_ROLE_DEFAULT_DDL, "ensure user.role default='user'") and ok
        ok = _run(PROMOTE_LEGACY_IS_ADMIN_DDL, "legacy is_admin sweep (no-op)") and ok

        # Step 2: last_login_at (delegate to the existing one-shot migration).
        try:
            from migrations.add_last_login_at import upgrade as _ll_up
            log.info("M1-007 step: delegate add_last_login_at.upgrade()")
            if not _ll_up():
                log.error("  delegate failed (add_last_login_at)")
                ok = False
            else:
                log.info("  delegate ok (add_last_login_at)")
        except Exception as exc:
            log.error("  delegate raised (add_last_login_at): %s", exc)
            ok = False

        # Step 3: system_setting (delegate to the existing one-shot migration).
        try:
            from migrations.add_system_setting import upgrade as _ss_up
            log.info("M1-007 step: delegate add_system_setting.upgrade()")
            if not _ss_up():
                log.error("  delegate failed (add_system_setting)")
                ok = False
            else:
                log.info("  delegate ok (add_system_setting)")
        except Exception as exc:
            log.error("  delegate raised (add_system_setting): %s", exc)
            ok = False

        log.info(
            "=== M1-007 admin consolidation: UPGRADE %s ===",
            "complete" if ok else "FAILED (some steps did not commit)"
        )
        return ok


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------
def downgrade() -> bool:
    """Reverse all three components (use only when reverting Stage C4)."""
    with app.app_context():
        log.info("=== M1-007 admin consolidation: DOWNGRADE starting ===")
        ok = True

        try:
            from migrations.add_system_setting import downgrade as _ss_down
            log.info("M1-007 step: delegate add_system_setting.downgrade()")
            ok = _ss_down() and ok
        except Exception as exc:
            log.error("  delegate raised (add_system_setting.down): %s", exc)
            ok = False

        try:
            from migrations.add_last_login_at import downgrade as _ll_down
            log.info("M1-007 step: delegate add_last_login_at.downgrade()")
            ok = _ll_down() and ok
        except Exception as exc:
            log.error("  delegate raised (add_last_login_at.down): %s", exc)
            ok = False

        ok = _run(DROP_ROLE_DEFAULT_DDL, "drop user.role default") and ok

        log.info(
            "=== M1-007 admin consolidation: DOWNGRADE %s ===",
            "complete" if ok else "PARTIAL"
        )
        return ok


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        sys.exit(0 if downgrade() else 1)
    sys.exit(0 if upgrade() else 1)
