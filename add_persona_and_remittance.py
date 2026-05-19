"""
Migration: add User.persona column + create remittance_entries table.
Idempotent. Safe to re-run.

Council-approved Wave A (FIESTA usefulness pivot 2026-05-16):
- User.persona: nullable VARCHAR(50). Existing users default NULL; new SL foreign-income
  earners opt in at signup. Routes diverge on this flag.
- remittance_entries: foreign-income ledger. Parallel to Invoice, not a refactor.
"""
import logging
from app import app, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def run():
    with app.app_context():
        with db.engine.connect() as conn:
            conn.execute(db.text('''
                ALTER TABLE "user"
                ADD COLUMN IF NOT EXISTS persona VARCHAR(50)
            '''))
            log.info("user.persona column ensured")

            conn.execute(db.text('''
                CREATE TABLE IF NOT EXISTS remittance_entries (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
                    organization_id INTEGER REFERENCES organization(id) ON DELETE SET NULL,

                    remittance_date DATE NOT NULL,
                    foreign_currency VARCHAR(3) NOT NULL,
                    foreign_amount NUMERIC(18, 2) NOT NULL,
                    lkr_amount_bank_rate NUMERIC(18, 2),

                    cbsl_rate NUMERIC(18, 6),
                    cbsl_rate_source VARCHAR(255),
                    cbsl_rate_captured_at TIMESTAMP,
                    lkr_amount_cbsl NUMERIC(18, 2),
                    rate_entered_manually BOOLEAN NOT NULL DEFAULT FALSE,

                    source_country VARCHAR(2),
                    payer_name VARCHAR(255),
                    sl_bank_account_id INTEGER REFERENCES bank_account(id) ON DELETE SET NULL,

                    source_doc_s3_key VARCHAR(512),
                    source_doc_filename VARCHAR(512),
                    bank_proof_s3_key VARCHAR(512),
                    bank_proof_filename VARCHAR(512),

                    foreign_tax_withheld_amount NUMERIC(18, 2),
                    foreign_tax_withheld_currency VARCHAR(3),
                    dta_certificate_s3_key VARCHAR(512),
                    dta_certificate_filename VARCHAR(512),

                    tax_year VARCHAR(7) NOT NULL,
                    notes TEXT,

                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            '''))
            log.info("remittance_entries table ensured")

            conn.execute(db.text('''
                CREATE INDEX IF NOT EXISTS ix_remittance_user_year
                    ON remittance_entries (user_id, tax_year)
            '''))
            conn.execute(db.text('''
                CREATE INDEX IF NOT EXISTS ix_remittance_org_year
                    ON remittance_entries (organization_id, tax_year)
            '''))
            log.info("indexes ensured")

            conn.commit()
            log.info("migration committed")


if __name__ == "__main__":
    run()
