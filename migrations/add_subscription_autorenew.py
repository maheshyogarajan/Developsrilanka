"""
Migration: Add auto-renew columns to paywall_subscription.

Tier D1 / C1 — Stripe subscription auto-renew + customer billing portal.

Adds 5 columns to the existing ``paywall_subscription`` table so the row can
ALSO represent a recurring Stripe Subscription (mode=subscription) rather than
just a one-time payment_intent. The two billing models coexist:

  * One-time   (legacy X1 / Self-File Rs 2,500): stripe_payment_intent_id set.
                Auto-renew columns all NULL/False.
  * Recurring  (Tier D1):                          stripe_subscription_id set.
                auto_renew=True, current_period_end populated by the
                invoice.paid webhook, cancel_at_period_end driven by the
                customer billing portal (or webhook updates).

A separate billing path means we DON'T have to migrate the existing one-time
rows. The webhook handler at /webhooks/stripe/subscription writes only to
recurring rows.

Idempotent via ADD COLUMN IF NOT EXISTS. Rollback via DROP COLUMN IF EXISTS.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


ADD_COLUMNS_DDL = """
ALTER TABLE paywall_subscription
ADD COLUMN IF NOT EXISTS auto_renew BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE paywall_subscription
ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255) NULL;

ALTER TABLE paywall_subscription
ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255) NULL;

ALTER TABLE paywall_subscription
ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMP NULL;

ALTER TABLE paywall_subscription
ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE;
"""

ADD_INDEXES_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_paywall_subscription_stripe_sub_id
    ON paywall_subscription (stripe_subscription_id);

CREATE INDEX IF NOT EXISTS ix_paywall_subscription_stripe_customer_id
    ON paywall_subscription (stripe_customer_id);
"""

DROP_COLUMNS_DDL = """
DROP INDEX IF EXISTS ix_paywall_subscription_stripe_sub_id;
DROP INDEX IF EXISTS ix_paywall_subscription_stripe_customer_id;
ALTER TABLE paywall_subscription DROP COLUMN IF EXISTS auto_renew;
ALTER TABLE paywall_subscription DROP COLUMN IF EXISTS stripe_subscription_id;
ALTER TABLE paywall_subscription DROP COLUMN IF EXISTS stripe_customer_id;
ALTER TABLE paywall_subscription DROP COLUMN IF EXISTS current_period_end;
ALTER TABLE paywall_subscription DROP COLUMN IF EXISTS cancel_at_period_end;
"""


def upgrade() -> bool:
    """Add the 5 auto-renew columns to paywall_subscription."""
    with app.app_context():
        try:
            log.info("add_subscription_autorenew: adding columns")
            for stmt in ADD_COLUMNS_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + auto-renew columns added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed at ADD COLUMN: %s", exc)
            return False

        try:
            log.info("add_subscription_autorenew: adding indexes")
            for stmt in ADD_INDEXES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + indexes added (or already existed)")
        except Exception as exc:
            db.session.rollback()
            log.warning("index creation non-fatal failure: %s", exc)

        log.info("add_subscription_autorenew: upgrade complete")
        return True


def downgrade() -> bool:
    """Drop the auto-renew columns + indexes."""
    with app.app_context():
        try:
            log.info("add_subscription_autorenew: dropping columns + indexes")
            for stmt in DROP_COLUMNS_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + auto-renew columns dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("downgrade failed: %s", exc)
            return False


if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        ok = downgrade()
    else:
        ok = upgrade()
    sys.exit(0 if ok else 1)
