"""fiesta.earnings.models — Statement + IncomeEntry persistence.

Schema notes:
  - `Statement.status` is a string enum to stay aligned with the existing
    fiesta convention (see RemittanceEntry, Receipt). Values:
      uploaded   → just landed; file on disk / S3
      processing → doc_lens extractor running
      extracted  → extraction returned rows; awaiting customer confirmation
      confirmed  → all extracted rows confirmed by the customer
      rejected   → 5 doc_lens attempts failed → manual-entry route taken
  - `IncomeEntry.category` mirrors the categories the tax engine expects.
    Sri Lanka IIT computation distinguishes: salary (APIT credit eligible),
    contractor_fee, foreign_remittance (remittance-basis treatment under
    SL tax law), interest (WHT credit eligible), dividend (final tax, no credit),
    rental.
  - `IncomeEntry.confirmed_by_customer` is the gate to_tax() reads — only
    confirmed=True rows aggregate. Edits flip confirmed=True after preserving
    original_value JSON.
  - `IncomeEntry.statement_id` is nullable: manual-entry rows have no statement.
  - `extracted_data` JSON on Statement carries the raw doc_lens payload for
    audit + debug. Never trust it for tax computation; the IncomeEntry rows
    are the source of truth.

Council #2 honest-uncertainty contract: extraction_confidence is a heuristic
not a calibrated probability. UI surfaces low-confidence rows for explicit
customer confirmation per CLAUDE.md Step 2b.
"""
from __future__ import annotations

import enum
from datetime import datetime, date
from app import db


# ---- Status / Type enums (string-valued; stored as VARCHAR) ----------------


class StatementStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class StatementDocType(str, enum.Enum):
    BANK_STATEMENT = "bank_statement"
    EMPLOYER_LETTER = "employer_letter"
    FOREIGN_INCOME_RECEIPT = "foreign_income_receipt"
    OTHER = "other"


class IncomeCategory(str, enum.Enum):
    SALARY = "salary"
    CONTRACTOR_FEE = "contractor_fee"
    FOREIGN_REMITTANCE = "foreign_remittance"
    INTEREST = "interest"
    DIVIDEND = "dividend"
    RENTAL = "rental"


# Maximum extraction attempts before mandatory manual-entry fallback.
# Per S4 spec: "5-attempt limit per statement before mandatory manual entry".
MAX_EXTRACTION_ATTEMPTS = 5

# Maximum file upload size in bytes (10MB per S4 spec).
MAX_FILE_BYTES = 10 * 1024 * 1024


class Statement(db.Model):
    """A customer-uploaded supporting document (bank statement / employer letter /
    foreign income receipt / other). The actual income line items extracted from
    this statement live in IncomeEntry rows joined by statement_id.
    """
    __tablename__ = "earnings_statements"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey("organization.id", ondelete="SET NULL"), nullable=True)

    # File metadata — file_path is filesystem path or s3_key (caller decides at upload time).
    file_path = db.Column(db.String(1024), nullable=False)
    file_name = db.Column(db.String(512), nullable=False)
    file_size_bytes = db.Column(db.Integer, nullable=False, default=0)
    file_sha256 = db.Column(db.String(64), nullable=True)  # de-dup hint
    storage_backend = db.Column(db.String(16), nullable=False, default="local")  # 'local' | 's3'

    # Doc type + status.
    doc_type = db.Column(db.String(32), nullable=False, default=StatementDocType.BANK_STATEMENT.value)
    bank_name = db.Column(db.String(255), nullable=True)
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(16), nullable=False, default=StatementStatus.UPLOADED.value, index=True)

    # Extraction telemetry.
    extraction_attempts = db.Column(db.Integer, nullable=False, default=0)
    extraction_confidence = db.Column(db.Float, nullable=True)
    extraction_method = db.Column(db.String(16), nullable=True)  # 'gemini'|'regex'|'none'
    extracted_data = db.Column(db.JSON, nullable=True)  # raw doc_lens payload (audit/debug)
    failure_reason = db.Column(db.Text, nullable=True)  # set when status=rejected

    # SL tax year string — '2025-26' format. Filled when known.
    tax_year = db.Column(db.String(7), nullable=True, index=True)

    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship — entries belong to a statement; orphan-delete cascade.
    entries = db.relationship(
        "IncomeEntry",
        backref="statement",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def is_extracting(self) -> bool:
        return self.status == StatementStatus.PROCESSING.value

    def is_extracted(self) -> bool:
        return self.status == StatementStatus.EXTRACTED.value

    def at_attempt_cap(self) -> bool:
        return self.extraction_attempts >= MAX_EXTRACTION_ATTEMPTS

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "file_name": self.file_name,
            "file_size_bytes": self.file_size_bytes,
            "doc_type": self.doc_type,
            "bank_name": self.bank_name,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "status": self.status,
            "extraction_attempts": self.extraction_attempts,
            "extraction_confidence": self.extraction_confidence,
            "extraction_method": self.extraction_method,
            "failure_reason": self.failure_reason,
            "tax_year": self.tax_year,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class IncomeEntry(db.Model):
    """A single income line item — extracted from a Statement OR entered manually.

    The tax engine reads `confirmed_by_customer=True` rows. Edits preserve the
    pre-edit value in `original_value` JSON so an audit can replay history.
    """
    __tablename__ = "earnings_income_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    statement_id = db.Column(
        db.Integer,
        db.ForeignKey("earnings_statements.id", ondelete="CASCADE"),
        nullable=True,  # NULL == manual entry
        index=True,
    )

    # Income line item fields.
    entry_date = db.Column(db.Date, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="LKR")
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    source = db.Column(db.String(255), nullable=True)  # employer / payer / "manual" / "other"
    category = db.Column(db.String(32), nullable=False, default=IncomeCategory.SALARY.value)

    # Currency conversion (filled at confirmation time or at to_tax aggregation).
    fx_rate_lkr = db.Column(db.Numeric(18, 6), nullable=True)
    fx_rate_source = db.Column(db.String(64), nullable=True)
    amount_lkr = db.Column(db.Numeric(18, 2), nullable=True)

    # Confirmation gate — to_tax() only aggregates confirmed=True rows.
    confirmed_by_customer = db.Column(db.Boolean, nullable=False, default=False)
    confirmed_at = db.Column(db.DateTime, nullable=True)

    # Edit history — JSON list of {field, old_value, new_value, edited_at} entries.
    # Lets the audit replay any customer corrections to extracted values.
    original_value = db.Column(db.JSON, nullable=True)

    # SL tax year string — '2025-26' format.
    tax_year = db.Column(db.String(7), nullable=False, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def is_manual(self) -> bool:
        return self.statement_id is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement_id": self.statement_id,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "currency": self.currency,
            "amount": float(self.amount) if self.amount is not None else None,
            "amount_lkr": float(self.amount_lkr) if self.amount_lkr is not None else None,
            "fx_rate_lkr": float(self.fx_rate_lkr) if self.fx_rate_lkr is not None else None,
            "fx_rate_source": self.fx_rate_source,
            "source": self.source,
            "category": self.category,
            "confirmed_by_customer": self.confirmed_by_customer,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "original_value": self.original_value,
            "tax_year": self.tax_year,
        }


def sl_tax_year_for(d: date) -> str:
    """Sri Lanka Y/A: 1 April → 31 March. Returns 'YYYY-YY' like '2025-26'.

    Mirrors remittance_models.current_sl_tax_year(on=…). Inline here to keep
    fiesta.earnings independent from remittance_models if the project ever
    splits them.
    """
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[2:]}"
