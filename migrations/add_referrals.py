"""
Migration: Add referral_code + referral_redemption tables.

Tier D4 / A3 - One-sided referral loop.

Adds two tables:

  * ``referral_code``       - one row per existing paid user. 8-char hex code
                              with uses cap + expiry.
  * ``referral_redemption`` - one row per new user who signs up with a referral
                              cookie. Tracks paid_at + referrer_credit_applied_at.

The Stripe coupon side-effect (auto-apply 20% off to the referrer's next
invoice) is wired in webhooks/stripe_subscription.py invoice.paid hook. NO
schema implications for paywall_subscription - we just write to the
referral_redemption row.

Idempotent via CREATE TABLE IF NOT EXISTS. Rollback via DROP TABLE.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


CREATE_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS referral_code (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES "user"(id),
    code            VARCHAR(16) NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP NULL,
    max_uses        INTEGER NOT NULL DEFAULT 100,
    uses_count      INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_referral_code_user_id
    ON referral_code (user_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_referral_code_code
    ON referral_code (code);

CREATE TABLE IF NOT EXISTS referral_redemption (
    id                            SERIAL PRIMARY KEY,
    code_id                       INTEGER NOT NULL REFERENCES referral_code(id),
    referee_user_id               INTEGER NOT NULL REFERENCES "user"(id),
    referee_subscription_id       VARCHAR(255) NULL,
    redeemed_at                   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at                       TIMESTAMP NULL,
    referrer_credit_applied_at    TIMESTAMP NULL,
    referrer_coupon_id            VARCHAR(255) NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_referral_redemption_referee
    ON referral_redemption (referee_user_id);

CREATE INDEX IF NOT EXISTS ix_referral_redemption_code_id
    ON referral_redemption (code_id);

CREATE INDEX IF NOT EXISTS ix_referral_redemption_referee_subscription
    ON referral_redemption (referee_subscription_id);
"""

DROP_TABLES_DDL = """
DROP INDEX IF EXISTS ix_referral_redemption_referee_subscription;
DROP INDEX IF EXISTS ix_referral_redemption_code_id;
DROP INDEX IF EXISTS ux_referral_redemption_referee;
DROP TABLE IF EXISTS referral_redemption;
DROP INDEX IF EXISTS ux_referral_code_code;
DROP INDEX IF EXISTS ux_referral_code_user_id;
DROP TABLE IF EXISTS referral_code;
"""


def upgrade() -> bool:
    """Create referral_code + referral_redemption tables + indexes."""
    with app.app_context():
        try:
            log.info("add_referrals: creating tables + indexes")
            for stmt in CREATE_TABLES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + referral tables created (or already existed)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("upgrade failed: %s", exc)
            return False


def downgrade() -> bool:
    """Drop referral tables + indexes."""
    with app.app_context():
        try:
            log.info("add_referrals: dropping tables + indexes")
            for stmt in DROP_TABLES_DDL.strip().split(";"):
                if stmt.strip():
                    db.session.execute(text(stmt))
            db.session.commit()
            log.info("  + referral tables dropped (or did not exist)")
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
