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

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def has_source_doc(self):
        return bool(self.source_doc_s3_key)

    def has_bank_proof(self):
        return bool(self.bank_proof_s3_key)

    def has_dta_certificate(self):
        return bool(self.dta_certificate_s3_key)

    def completeness_status(self):
        """GPT's completeness-badge pattern. Returns ('ird_ready'|'partial'|'missing', label)."""
        missing = []
        if not self.has_source_doc():
            missing.append("source document")
        if not self.has_bank_proof():
            missing.append("SL bank proof")
        if self.cbsl_rate is None:
            missing.append("CBSL rate")
        if not missing:
            return ("ird_ready", "IRD-ready")
        if len(missing) == 1:
            return ("partial", f"Missing {missing[0]}")
        return ("missing", f"Missing {', '.join(missing)}")
