"""
Enhanced Financial Models for Universal Transaction System
This module implements the normalized transaction architecture with full GL integration.
"""

from app import db
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

class TransactionStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    RECONCILED = "reconciled"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"

class ReconciliationStatus(Enum):
    UNMATCHED = "unmatched"
    AUTO_MATCHED = "auto_matched"
    MANUAL_MATCHED = "manual_matched"
    DISPUTED = "disputed"
    CONFIRMED = "confirmed"

class SourceType(Enum):
    RECEIPT_SCAN = "receipt_scan"
    BANK_STATEMENT = "bank_statement"
    MANUAL_ENTRY = "manual_entry"
    JOURNAL_ENTRY = "journal_entry"
    ECOMMERCE = "ecommerce"
    PAYROLL = "payroll"

class FundingSourceType(Enum):
    BANK_ACCOUNT = "bank_account"
    DIRECTORS_LOAN = "directors_loan"
    CREDIT_CARD = "credit_card"
    CASH = "cash"
    ACCOUNTS_PAYABLE = "accounts_payable"

# Core universal transaction table
class FinancialTransaction(db.Model):
    """Universal transaction table for all financial activities."""
    __tablename__ = 'financial_transaction'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    
    # Core transaction data
    transaction_date = db.Column(db.Date, nullable=False)
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    description = db.Column(db.Text, nullable=False)
    
    # Multi-currency support (IFRS 21 compliance)
    currency_code = db.Column(db.String(3), default='USD')
    fx_rate = db.Column(db.Numeric(12, 6), default=Decimal('1.000000'))
    base_currency_amount = db.Column(db.Numeric(15, 2))  # Auto-calculated: amount * fx_rate
    
    # Source tracking
    source_type = db.Column(db.String(20), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)  # References transaction_meta_* tables
    
    # GL Integration (every transaction creates GL entries)
    debit_account_code = db.Column(db.String(20), nullable=True)  # Will be populated after account mapping
    credit_account_code = db.Column(db.String(20), nullable=True)
    ledger_entry_id = db.Column(db.Integer, db.ForeignKey('general_ledger_entry.id'), nullable=True)
    
    # Status and reconciliation
    status = db.Column(db.String(20), default=TransactionStatus.PENDING.value)
    reconciliation_status = db.Column(db.String(20), default=ReconciliationStatus.UNMATCHED.value)
    matched_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=True)
    reconciliation_confidence = db.Column(db.Numeric(5, 2), nullable=True)
    
    # Service charge handling
    service_charge = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    net_amount = db.Column(db.Numeric(15, 2))  # amount - service_charge
    
    # Audit trail
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    approved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    
    # Relationships
    organization = db.relationship('Organization', backref='financial_transactions')
    created_by_user = db.relationship('User', foreign_keys=[created_by], backref='created_transactions')
    approved_by_user = db.relationship('User', foreign_keys=[approved_by], backref='approved_transactions')
    matched_transaction = db.relationship('FinancialTransaction', remote_side=[id])
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-calculate base currency amount
        if self.amount and self.fx_rate:
            self.base_currency_amount = self.amount * self.fx_rate
        if self.amount and self.service_charge:
            self.net_amount = self.amount - self.service_charge
    
    def to_dict(self):
        """Convert transaction to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'transaction_date': self.transaction_date.isoformat() if self.transaction_date else None,
            'amount': float(self.amount) if self.amount else 0,
            'description': self.description,
            'currency_code': self.currency_code,
            'fx_rate': float(self.fx_rate) if self.fx_rate else 1.0,
            'base_currency_amount': float(self.base_currency_amount) if self.base_currency_amount else 0,
            'source_type': self.source_type,
            'source_id': self.source_id,
            'debit_account_code': self.debit_account_code,
            'credit_account_code': self.credit_account_code,
            'status': self.status,
            'reconciliation_status': self.reconciliation_status,
            'reconciliation_confidence': float(self.reconciliation_confidence) if self.reconciliation_confidence else None,
            'service_charge': float(self.service_charge) if self.service_charge else 0,
            'net_amount': float(self.net_amount) if self.net_amount else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None
        }

# Receipt-specific metadata (normalized)
class TransactionMetaReceipt(db.Model):
    """Receipt-specific metadata linked to financial transactions."""
    __tablename__ = 'transaction_meta_receipt'
    
    transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), primary_key=True)
    
    # Receipt-specific fields
    vendor_name = db.Column(db.String(255), nullable=False)
    vendor_address = db.Column(db.Text, nullable=True)
    vendor_contact = db.Column(db.String(255), nullable=True)
    vat_registration_number = db.Column(db.String(100), nullable=True)
    
    # Tax details
    service_charge = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    sscl_tax = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    vat_tax = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    
    # Image storage
    s3_key = db.Column(db.String(500), nullable=True)
    thumbnail_s3_key = db.Column(db.String(500), nullable=True)
    
    # AI extraction metadata
    extraction_confidence = db.Column(db.Numeric(5, 2), nullable=True)
    extraction_model = db.Column(db.String(100), nullable=True)
    correction_count = db.Column(db.Integer, default=0)
    original_extracted_text = db.Column(db.Text, nullable=True)
    
    # Categorization
    expense_major_category = db.Column(db.String(100), nullable=True)
    expense_minor_category = db.Column(db.String(100), nullable=True)
    
    # Relationships
    transaction = db.relationship('FinancialTransaction', backref='receipt_meta')
    
    def to_dict(self):
        """Convert receipt metadata to dictionary."""
        return {
            'transaction_id': self.transaction_id,
            'vendor_name': self.vendor_name,
            'vendor_address': self.vendor_address or '',
            'vendor_contact': self.vendor_contact or '',
            'vat_registration_number': self.vat_registration_number or '',
            'service_charge': float(self.service_charge) if self.service_charge else 0,
            'sscl_tax': float(self.sscl_tax) if self.sscl_tax else 0,
            'vat_tax': float(self.vat_tax) if self.vat_tax else 0,
            's3_key': self.s3_key or '',
            'thumbnail_s3_key': self.thumbnail_s3_key or '',
            'extraction_confidence': float(self.extraction_confidence) if self.extraction_confidence else None,
            'extraction_model': self.extraction_model or '',
            'correction_count': self.correction_count,
            'expense_major_category': self.expense_major_category or '',
            'expense_minor_category': self.expense_minor_category or ''
        }

# Bank statement metadata
class TransactionMetaBank(db.Model):
    """Bank statement-specific metadata linked to financial transactions."""
    __tablename__ = 'transaction_meta_bank'
    
    transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), primary_key=True)
    
    # Bank-specific fields
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id'), nullable=True)
    bank_reference_number = db.Column(db.String(100), nullable=True)
    statement_line_number = db.Column(db.Integer, nullable=True)
    running_balance = db.Column(db.Numeric(15, 2), nullable=True)
    
    # Processing metadata
    extraction_confidence = db.Column(db.Numeric(5, 2), nullable=True)
    dual_pass_validated = db.Column(db.Boolean, default=False)
    discrepancies_found = db.Column(db.Boolean, default=False)
    
    # Relationships
    transaction = db.relationship('FinancialTransaction', backref='bank_meta')
    bank_statement = db.relationship('BankStatement', backref='transaction_metas')
    
    def to_dict(self):
        """Convert bank metadata to dictionary."""
        return {
            'transaction_id': self.transaction_id,
            'bank_statement_id': self.bank_statement_id,
            'bank_reference_number': self.bank_reference_number or '',
            'statement_line_number': self.statement_line_number,
            'running_balance': float(self.running_balance) if self.running_balance else None,
            'extraction_confidence': float(self.extraction_confidence) if self.extraction_confidence else None,
            'dual_pass_validated': self.dual_pass_validated,
            'discrepancies_found': self.discrepancies_found
        }

# Manual entry metadata
class TransactionMetaManual(db.Model):
    """Manual entry-specific metadata linked to financial transactions."""
    __tablename__ = 'transaction_meta_manual'
    
    transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), primary_key=True)
    
    # Manual entry fields
    entry_reason = db.Column(db.String(255), nullable=True)  # 'lost_receipt', 'accrual', 'adjustment', 'transfer'
    supporting_documentation = db.Column(db.Text, nullable=True)
    approval_notes = db.Column(db.Text, nullable=True)
    requires_documentation = db.Column(db.Boolean, default=True)
    
    # Relationships
    transaction = db.relationship('FinancialTransaction', backref='manual_meta')
    
    def to_dict(self):
        """Convert manual metadata to dictionary."""
        return {
            'transaction_id': self.transaction_id,
            'entry_reason': self.entry_reason or '',
            'supporting_documentation': self.supporting_documentation or '',
            'approval_notes': self.approval_notes or '',
            'requires_documentation': self.requires_documentation
        }

# Journal entry metadata
class TransactionMetaJournal(db.Model):
    """Journal entry-specific metadata linked to financial transactions."""
    __tablename__ = 'transaction_meta_journal'
    
    transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), primary_key=True)
    
    # Journal-specific fields
    journal_type = db.Column(db.String(50), nullable=True)  # 'depreciation', 'accrual', 'adjustment', 'closing'
    period_end_date = db.Column(db.Date, nullable=True)
    reversal_date = db.Column(db.Date, nullable=True)  # For accruals that reverse
    recurring = db.Column(db.Boolean, default=False)
    recurring_frequency = db.Column(db.String(20), nullable=True)  # 'monthly', 'quarterly', 'annually'
    
    # Relationships
    transaction = db.relationship('FinancialTransaction', backref='journal_meta')
    
    def to_dict(self):
        """Convert journal metadata to dictionary."""
        return {
            'transaction_id': self.transaction_id,
            'journal_type': self.journal_type or '',
            'period_end_date': self.period_end_date.isoformat() if self.period_end_date else None,
            'reversal_date': self.reversal_date.isoformat() if self.reversal_date else None,
            'recurring': self.recurring,
            'recurring_frequency': self.recurring_frequency or ''
        }

# Enhanced bank statement system
class BankStatement(db.Model):
    """Bank statements with enhanced duplicate prevention."""
    __tablename__ = 'bank_statement'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    bank_account_id = db.Column(db.Integer, db.ForeignKey('bank_account.id'), nullable=True)
    
    # Enhanced duplicate prevention (IFRS compliant)
    content_sha256 = db.Column(db.String(64), unique=True, nullable=False)
    file_size_bytes = db.Column(db.BigInteger, nullable=False)
    page_count = db.Column(db.Integer, nullable=False)
    
    # Statement metadata
    bank_name = db.Column(db.String(255), nullable=True)
    account_number = db.Column(db.String(100), nullable=True)
    statement_period_from = db.Column(db.Date, nullable=False)
    statement_period_to = db.Column(db.Date, nullable=False)
    opening_balance = db.Column(db.Numeric(15, 2), nullable=True)
    closing_balance = db.Column(db.Numeric(15, 2), nullable=True)
    statement_currency = db.Column(db.String(3), default='USD')
    
    # File storage
    original_pdf_s3_key = db.Column(db.String(500), nullable=True)
    password_protected = db.Column(db.Boolean, default=False)
    
    # Processing status
    processing_status = db.Column(db.String(20), default='uploaded')
    processing_notes = db.Column(db.Text, nullable=True)  # For audit trail storage
    
    # Reconciliation statistics
    total_transactions = db.Column(db.Integer, default=0)
    auto_matched_count = db.Column(db.Integer, default=0)
    manual_matched_count = db.Column(db.Integer, default=0)
    exception_count = db.Column(db.Integer, default=0)
    
    # Audit trail
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    processed_at = db.Column(db.DateTime, nullable=True)
    reconciled_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='bank_statements')
    uploaded_by_user = db.relationship('User', backref='uploaded_statements')
    pages = db.relationship('BankStatementPage', backref='statement', lazy=True, cascade='all, delete-orphan')
    
    @staticmethod
    def generate_content_hash(file_content, file_size, account_number, period_from, period_to):
        """Generate SHA-256 hash for duplicate detection."""
        hash_input = f"{file_size}|{account_number}|{period_from}|{period_to}".encode()
        hash_input += file_content[:1024]  # Include first 1KB of file content
        return hashlib.sha256(hash_input).hexdigest()
    
    def to_dict(self):
        """Convert bank statement to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'bank_account_id': self.bank_account_id,
            'content_sha256': self.content_sha256,
            'file_size_bytes': self.file_size_bytes,
            'page_count': self.page_count,
            'bank_name': self.bank_name or '',
            'account_number': self.account_number or '',
            'statement_period_from': self.statement_period_from.isoformat() if self.statement_period_from else None,
            'statement_period_to': self.statement_period_to.isoformat() if self.statement_period_to else None,
            'opening_balance': float(self.opening_balance) if self.opening_balance else None,
            'closing_balance': float(self.closing_balance) if self.closing_balance else None,
            'statement_currency': self.statement_currency,
            'processing_status': self.processing_status,
            'total_transactions': self.total_transactions,
            'auto_matched_count': self.auto_matched_count,
            'manual_matched_count': self.manual_matched_count,
            'exception_count': self.exception_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None
        }

# Enhanced page tracking with dual hashing
class BankStatementPage(db.Model):
    """Individual bank statement pages with dual hash system."""
    __tablename__ = 'bank_statement_page'
    
    id = db.Column(db.Integer, primary_key=True)
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id'), nullable=False)
    page_number = db.Column(db.Integer, nullable=False)
    
    # Dual hash system for performance
    content_sha256 = db.Column(db.String(64), unique=True, nullable=False)
    content_mmh3 = db.Column(db.Integer, nullable=False)  # MurmurHash3 for fast pre-filtering
    
    image_s3_key = db.Column(db.String(500), nullable=True)
    
    # Dual-pass extraction validation
    extraction_status = db.Column(db.String(20), default='pending')
    pass1_transaction_count = db.Column(db.Integer, nullable=True)
    pass2_transaction_count = db.Column(db.Integer, nullable=True)
    extraction_discrepancies = db.Column(db.JSON, nullable=True)  # Array of discrepancy descriptions
    
    validation_confidence = db.Column(db.Numeric(5, 2), nullable=True)
    manual_review_required = db.Column(db.Boolean, default=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        """Convert bank statement page to dictionary."""
        return {
            'id': self.id,
            'bank_statement_id': self.bank_statement_id,
            'page_number': self.page_number,
            'content_sha256': self.content_sha256,
            'content_mmh3': self.content_mmh3,
            'image_s3_key': self.image_s3_key or '',
            'extraction_status': self.extraction_status,
            'pass1_transaction_count': self.pass1_transaction_count,
            'pass2_transaction_count': self.pass2_transaction_count,
            'extraction_discrepancies': self.extraction_discrepancies or [],
            'validation_confidence': float(self.validation_confidence) if self.validation_confidence else None,
            'manual_review_required': self.manual_review_required,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Processing progress tracking
class BankStatementProcessingLog(db.Model):
    """Processing progress tracking for better UX."""
    __tablename__ = 'bank_statement_processing_log'
    
    id = db.Column(db.Integer, primary_key=True)
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id'), nullable=False)
    
    processing_stage = db.Column(db.String(50), nullable=False)  # 'upload', 'extraction', 'validation', 'reconciliation'
    stage_status = db.Column(db.String(20), nullable=False)  # 'started', 'completed', 'error', 'requires_password'
    progress_percentage = db.Column(db.Integer, default=0)
    current_page = db.Column(db.Integer, nullable=True)
    total_pages = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    bank_statement = db.relationship('BankStatement', backref='processing_logs')
    
    def to_dict(self):
        """Convert processing log to dictionary."""
        return {
            'id': self.id,
            'bank_statement_id': self.bank_statement_id,
            'processing_stage': self.processing_stage,
            'stage_status': self.stage_status,
            'progress_percentage': self.progress_percentage,
            'current_page': self.current_page,
            'total_pages': self.total_pages,
            'error_message': self.error_message or '',
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Universal funding source system
class FundingSource(db.Model):
    """Universal funding source tracking."""
    __tablename__ = 'funding_source'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    
    # Funding details
    source_type = db.Column(db.String(20), nullable=False)
    source_name = db.Column(db.String(255), nullable=False)
    account_code = db.Column(db.String(20), nullable=True)  # Links to chart of accounts
    
    # Balance tracking
    current_balance = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    last_reconciled_balance = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    last_reconciled_date = db.Column(db.Date, nullable=True)
    
    # Currency support
    currency_code = db.Column(db.String(3), default='USD')
    
    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='funding_sources')
    transactions = db.relationship('FundingSourceTransaction', backref='funding_source', lazy=True)
    
    def to_dict(self):
        """Convert funding source to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'source_type': self.source_type,
            'source_name': self.source_name,
            'account_code': self.account_code or '',
            'current_balance': float(self.current_balance) if self.current_balance else 0,
            'last_reconciled_balance': float(self.last_reconciled_balance) if self.last_reconciled_balance else 0,
            'last_reconciled_date': self.last_reconciled_date.isoformat() if self.last_reconciled_date else None,
            'currency_code': self.currency_code,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

# Funding source transactions
class FundingSourceTransaction(db.Model):
    """Universal transaction tracking for all funding sources."""
    __tablename__ = 'funding_source_transaction'
    
    id = db.Column(db.Integer, primary_key=True)
    funding_source_id = db.Column(db.Integer, db.ForeignKey('funding_source.id'), nullable=False)
    financial_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=False)
    
    # Transaction impact
    transaction_type = db.Column(db.String(20), nullable=False)  # 'debit', 'credit'
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    balance_after = db.Column(db.Numeric(15, 2), nullable=False)
    
    # GL linkage
    ledger_entry_id = db.Column(db.Integer, db.ForeignKey('general_ledger_entry.id'), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    financial_transaction = db.relationship('FinancialTransaction', backref='funding_transactions')
    ledger_entry = db.relationship('GeneralLedgerEntry', backref='funding_transactions')
    
    def to_dict(self):
        """Convert funding source transaction to dictionary."""
        return {
            'id': self.id,
            'funding_source_id': self.funding_source_id,
            'financial_transaction_id': self.financial_transaction_id,
            'transaction_type': self.transaction_type,
            'amount': float(self.amount) if self.amount else 0,
            'balance_after': float(self.balance_after) if self.balance_after else 0,
            'ledger_entry_id': self.ledger_entry_id,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }