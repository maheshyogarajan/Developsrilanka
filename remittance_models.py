"""
RemittanceEntry — foreign-income ledger for SL foreign-income earners.
Parallel to Invoice/Client; does NOT touch existing models.

Council Wave A 2026-05-16 (FIESTA_USEFULNESS_REVIEW.md).
"""
from datetime import datetime, date
from app import db


PERSONA_SL_FOREIGN_INCOME = "sl_foreign_income"


def current_sl_tax_year(on=None):
    """Sri Lanka Y/A runs 1 April → 31 March. Returns 'YYYY-YY' e.g. '2025-26'."""
    d = on or date.today()
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"


class RemittanceEntry(db.Model):
    __tablename__ = "remittance_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey("organization.id", ondelete="SET NULL"), nullable=True)

    remittance_date = db.Column(db.Date, nullable=False)
    foreign_currency = db.Column(db.String(3), nullable=False)
    foreign_amount = db.Column(db.Numeric(18, 2), nullable=False)
    lkr_amount_bank_rate = db.Column(db.Numeric(18, 2), nullable=True)

    cbsl_rate = db.Column(db.Numeric(18, 6), nullable=True)
    cbsl_rate_source = db.Column(db.String(255), nullable=True)
    cbsl_rate_captured_at = db.Column(db.DateTime, nullable=True)
    lkr_amount_cbsl = db.Column(db.Numeric(18, 2), nullable=True)
    rate_entered_manually = db.Column(db.Boolean, nullable=False, default=False)

    source_country = db.Column(db.String(2), nullable=True)
    payer_name = db.Column(db.String(255), nullable=True)
    sl_bank_account_id = db.Column(db.Integer, db.ForeignKey("bank_account.id", ondelete="SET NULL"), nullable=True)

    source_doc_s3_key = db.Column(db.String(512), nullable=True)
    source_doc_filename = db.Column(db.String(512), nullable=True)
    bank_proof_s3_key = db.Column(db.String(512), nullable=True)
    bank_proof_filename = db.Column(db.String(512), nullable=True)

    foreign_tax_withheld_amount = db.Column(db.Numeric(18, 2), nullable=True)
    foreign_tax_withheld_currency = db.Column(db.String(3), nullable=True)
    dta_certificate_s3_key = db.Column(db.String(512), nullable=True)
    dta_certificate_filename = db.Column(db.String(512), nullable=True)

    tax_year = db.Column(db.String(7), nullable=False)
    notes = db.Column(db.Text, nullable=True)

    # Wave H 2026-05-17 (council #1): the "IRD-ready" badge is suppressed until a Lanka.tax
    # staff member has reviewed the entry against current IRD format expectations.
    # Premortem T6 mitigation. Defaults False; flipped by a staff-only endpoint (TBD Wave I).
    ird_ready_staff_reviewed = db.Column(db.Boolean, nullable=False, default=False)

    # MS2 E.0 / Design Lock 2 §4 — soft-link to canonical Income row.
    # Nullable initially; backfill migration 20260525_130100_e_b8_schema.py
    # creates one Income row per existing RemittanceEntry and populates this
    # FK. New remittances should create an Income row in the same transaction.
    # ondelete='SET NULL' so the Income row outlives a remittance deletion.
    income_id = db.Column(
        db.Integer,
        db.ForeignKey("incomes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def has_source_doc(self):
        return bool(self.source_doc_s3_key)

    def has_bank_proof(self):
        return bool(self.bank_proof_s3_key)

    def has_dta_certificate(self):
        return bool(self.dta_certificate_s3_key)

    def completeness_status(self):
        """Council #1 fix (Wave H, punch #19): IRD-ready never fires from user input alone —
        requires Lanka.tax staff review (ird_ready_staff_reviewed=True). Returns
        ('ird_ready'|'partial'|'missing'|'evidence_ready', label)."""
        missing = []
        if not self.has_source_doc():
            missing.append("source document")
        if not self.has_bank_proof():
            missing.append("SL bank proof")
        if self.cbsl_rate is None:
            missing.append("CBSL rate")
        if missing:
            if len(missing) == 1:
                return ("partial", f"Missing {missing[0]}")
            return ("missing", f"Missing {', '.join(missing)}")
        # All evidence collected — but IRD-ready requires Lanka.tax staff review.
        if self.ird_ready_staff_reviewed:
            return ("ird_ready", "IRD-ready (staff reviewed)")
        return ("evidence_ready", "Evidence complete · awaiting Lanka.tax review")


class RemittanceImportBatch(db.Model):
    """Server-side store for import candidates between upload→review→confirm.

    Council #1 Wave H (H2) — session-cookie storage caps at ~4KB which is broken for
    realistic >50-row SL bank statements. This table holds candidates server-side,
    keyed by a UUID handed to the user via URL.

    Auto-expires after 24h via the `expires_at` column (housekeeping cron, deferred).
    """
    __tablename__ = "remittance_import_batches"

    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(db.String(12), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    filename = db.Column(db.String(512), nullable=False)
    kind = db.Column(db.String(16), nullable=False)          # 'pdf' | 'csv' | 'unknown'
    candidates = db.Column(db.JSON, nullable=False)          # normalised list of dicts
    file_sha256 = db.Column(db.String(64), nullable=True)    # H8 duplicate detection
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    consumed_at = db.Column(db.DateTime, nullable=True)      # set when /confirm runs
