"""
Migration: Create system_setting table and seed defaults.

Feature C9 F8.8 (PLAN_X9_COMPLETION §4 Stage C.2).

Follows the same raw-SQL pattern as migrations/add_al_tables.py
(shared app + db, SQLAlchemy text(), rollback-safe, idempotent via
CREATE TABLE IF NOT EXISTS).

Table created:
  system_setting(key PK, value TEXT, updated_at TIMESTAMP, updated_by_user_id INT)

Seed rows:
  Derived from the module-global dicts in admin_routes.py that this table
  replaces. Values are JSON-encoded. Existing seed rows are NOT overwritten
  (ON CONFLICT DO NOTHING) so re-running the migration is safe.

  Tax settings (from admin_routes.tax_settings):
    lkr_business_tax_rate   = 36.0
    usd_consulting_tax_rate = 15.0
    vat_rate                = 8.0
    sscl_rate               = 2.0

  General settings (from admin_routes.general_settings):
    app_name              = "Sri Lanka Tax Optimizer"
    items_per_page        = 10
    default_currency      = "LKR"
    enable_registration   = true
    enable_ai_features    = true

  FIESTA tax constants (from sri_lanka_tax_rules.TaxDeductibilityClassifier):
    business_tax_rate      = 0.36   (36% LKR business income 2024/2025)
    usd_consulting_tax_rate_legacy = 0.18 (18% USD consulting; same as above, kept for audit trail)

  Note: The authoritative tax brackets + personal relief for FIESTA ITIT
  computation live in fiesta/tax/data/slabs.yaml — those are NOT seeded here
  because they are version-pinned YAML files, not runtime-editable settings.
  SystemSetting is for operator-tunable values, not gazette-sourced brackets.

Downgrade: DROP TABLE IF EXISTS (safe — no FK into this table).
"""

import json
import logging
from datetime import datetime
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
CREATE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS system_setting (
    key                  VARCHAR(128) PRIMARY KEY,
    value                TEXT,
    updated_at           TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_by_user_id   INTEGER REFERENCES "user"(id) ON DELETE SET NULL
);
"""

DROP_TABLE_DDL = "DROP TABLE IF EXISTS system_setting;"


# ---------------------------------------------------------------------------
# Seed defaults
# Derived from admin_routes.py module globals + sri_lanka_tax_rules.py.
# ON CONFLICT DO NOTHING so re-running this migration is idempotent.
# ---------------------------------------------------------------------------
_SEED_SETTINGS = [
    # From admin_routes.tax_settings (module-global dict)
    ("lkr_business_tax_rate",         36.0,                     "LKR business income tax rate (%) — from admin_routes.tax_settings"),
    ("usd_consulting_tax_rate",        15.0,                     "USD consulting income tax rate (%) — from admin_routes.tax_settings"),
    ("vat_rate",                       8.0,                      "VAT rate (%) — from admin_routes.tax_settings"),
    ("sscl_rate",                      2.0,                      "SSCL rate (%) — from admin_routes.tax_settings"),

    # From admin_routes.general_settings (module-global dict)
    ("app_name",                       "Sri Lanka Tax Optimizer", "Application display name"),
    ("items_per_page",                 10,                        "Default pagination items per page"),
    ("default_currency",               "LKR",                     "Default currency for new accounts"),
    ("enable_registration",            True,                      "Enable/disable new user registration"),
    ("enable_ai_features",             True,                      "Enable/disable Gemini Vision and other AI features"),

    # From sri_lanka_tax_rules.TaxDeductibilityClassifier (module globals)
    ("classifier_business_tax_rate",   0.36,                     "TaxDeductibilityClassifier.BUSINESS_TAX_RATE — 36% for LKR business income (2024/2025)"),
    ("classifier_usd_consulting_rate", 0.18,                     "TaxDeductibilityClassifier.USD_CONSULTING_TAX_RATE — 18% for USD consulting income"),

    # FIESTA solar cap (from fiesta/tax/relief.py SOLAR_RELIEF_CAP_LKR)
    ("solar_relief_cap_lkr",           600000,                   "Max solar investment deduction (LKR) — fiesta.tax.relief.SOLAR_RELIEF_CAP_LKR; gazette-sourced; change requires IRD validation"),
]


def _seed_settings(conn):
    """Insert defaults ON CONFLICT DO NOTHING (idempotent)."""
    for key, value, _comment in _SEED_SETTINGS:
        encoded = json.dumps(value)
        conn.execute(
            text(
                "INSERT INTO system_setting (key, value, updated_at) "
                "VALUES (:key, :value, :now) "
                "ON CONFLICT (key) DO NOTHING;"
            ),
            {"key": key, "value": encoded, "now": datetime.utcnow()},
        )
        log.info("  seeded: %s = %s", key, encoded)


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------
def upgrade():
    """Create system_setting table and seed defaults (idempotent)."""
    with app.app_context():
        try:
            log.info("Migration add_system_setting: creating table")
            db.session.execute(text(CREATE_TABLE_DDL))
            db.session.commit()
            log.info("  ✓ system_setting table created (or already existed)")

            log.info("Migration add_system_setting: seeding defaults")
            _seed_settings(db.session)
            db.session.commit()
            log.info("  ✓ seed rows inserted (ON CONFLICT DO NOTHING)")

            log.info("Migration add_system_setting: upgrade complete")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration upgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Downgrade (rollback)
# ---------------------------------------------------------------------------
def downgrade():
    """Drop system_setting table (safe — no FK into it from other tables)."""
    with app.app_context():
        try:
            log.info("Migration add_system_setting: dropping table")
            db.session.execute(text(DROP_TABLE_DDL))
            db.session.commit()
            log.info("  ✓ system_setting table dropped (or did not exist)")
            return True
        except Exception as exc:
            db.session.rollback()
            log.error("Migration downgrade failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"
    if action == "downgrade":
        ok = downgrade()
    else:
        ok = upgrade()
    sys.exit(0 if ok else 1)
