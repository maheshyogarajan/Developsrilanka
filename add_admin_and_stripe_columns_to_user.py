"""
Migration: additive columns on ``user`` for the FIESTA admin surface
(Wave 6, 2026-05-20).

Adds two columns. Both are idempotent (``ADD COLUMN IF NOT EXISTS``), safe to
re-run, and run automatically at app boot via ``main.py`` (next to the existing
``add_tos_privacy_acceptance_to_user.run()`` call).

1. ``user.is_admin BOOLEAN NOT NULL DEFAULT FALSE``
   Boolean column mirroring the existing ``User.is_admin()`` method (which
   returns ``role == 'admin'``). Existing rows with ``role='admin'`` are
   backfilled to ``TRUE`` so the two readers stay consistent.

   IMPORTANT: the User model in ``models.py`` keeps the ``is_admin()`` method
   as the authoritative reader for the 21+ existing call sites. The new
   ``fiesta/auth/decorators.py`` reader handles either shape, so this column
   is forward-looking — it lets a future model refactor expose ``is_admin``
   as a direct attribute without breaking anything.

2. ``user.stripe_customer_id VARCHAR(255)``
   Cached Stripe ``cus_*`` id, populated by the Stripe webhook handler when
   a checkout session converts. Used by the S15 admin users page to render a
   "Open in Stripe" link **without** a live API call. The webhook writer can
   land in a follow-up commit — this is a pure additive column for now.

Schema authority
----------------
Same pattern as ``add_persona_and_remittance.py`` and
``add_tos_privacy_acceptance_to_user.py``: raw SQL via SQLAlchemy ``db.text``,
inside an explicit transaction, guarded by ``IF NOT EXISTS``.
"""
from __future__ import annotations

import logging

from app import app, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def run() -> dict:
    """Apply the migration. Returns a small summary dict for the boot log."""
    summary = {
        "is_admin_added": False,
        "stripe_customer_id_added": False,
        "backfilled_role_admins": 0,
    }
    with app.app_context():
        with db.engine.connect() as conn:
            # 1) Boolean is_admin column. The DEFAULT FALSE keeps Postgres
            # from refusing the ADD on a non-empty table.
            res = conn.execute(db.text("""
                ALTER TABLE "user"
                ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE
            """))
            summary["is_admin_added"] = True
            log.info("user.is_admin column ensured (BOOLEAN NOT NULL DEFAULT FALSE)")

            # 2) Cached Stripe customer id (cus_XXX). Nullable — only users
            # who have completed a Stripe checkout get one.
            conn.execute(db.text("""
                ALTER TABLE "user"
                ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)
            """))
            summary["stripe_customer_id_added"] = True
            log.info("user.stripe_customer_id column ensured")

            # 3) Backfill: any user whose role='admin' (the legacy authority)
            # should also have is_admin=TRUE so the two readers agree.
            backfill = conn.execute(db.text("""
                UPDATE "user"
                SET is_admin = TRUE
                WHERE role = 'admin' AND is_admin IS NOT TRUE
            """))
            # SQLAlchemy 2.x: rowcount is on the result proxy
            try:
                summary["backfilled_role_admins"] = backfill.rowcount or 0
            except Exception:
                summary["backfilled_role_admins"] = 0
            log.info(
                "user.is_admin backfilled from role='admin' "
                f"({summary['backfilled_role_admins']} row(s))"
            )

            conn.commit()
            log.info("migration committed: add_admin_and_stripe_columns_to_user")
    return summary


if __name__ == "__main__":
    print(run())
