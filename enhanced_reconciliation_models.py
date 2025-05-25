"""
Enhanced Reconciliation Models for Smart Bank Statement Matching
This module implements the learning reconciliation system with ML predictions.
"""

from app import db
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)

class ExceptionType(Enum):
    BANK_FEE = "bank_fee"
    TIMING_DIFF = "timing_diff"
    ERROR = "error"
    SPLIT_TRANSACTION = "split_transaction"
    AGGREGATED_SETTLEMENT = "aggregated_settlement"

class ResolutionStatus(Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    PERMANENT_DIFFERENCE = "permanent_difference"

# Reconciliation exceptions (no GL bypass)
class ReconciliationException(db.Model):
    """Reconciliation exceptions with formal GL entries."""
    __tablename__ = 'reconciliation_exception'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id'), nullable=True)
    
    # Exception details
    exception_type = db.Column(db.String(20), nullable=False)
    exception_amount = db.Column(db.Numeric(15, 2), nullable=False)
    variance_amount = db.Column(db.Numeric(15, 2), nullable=True)  # Difference that needs explanation
    
    # Source transactions
    bank_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=True)
    system_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=True)
    
    # Resolution
    resolution_status = db.Column(db.String(20), default=ResolutionStatus.OPEN.value)
    resolution_notes = db.Column(db.Text, nullable=True)
    resolver_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    
    # GL impact (exceptions create formal entries)
    adjustment_ledger_entry_id = db.Column(db.Integer, db.ForeignKey('general_ledger_entry.id'), nullable=True)
    
    # Audit trail
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='reconciliation_exceptions')
    bank_statement = db.relationship('BankStatement', backref='exceptions')
    bank_transaction = db.relationship('FinancialTransaction', foreign_keys=[bank_transaction_id])
    system_transaction = db.relationship('FinancialTransaction', foreign_keys=[system_transaction_id])
    resolver = db.relationship('User', foreign_keys=[resolver_user_id])
    created_by_user = db.relationship('User', foreign_keys=[created_by])
    adjustment_ledger_entry = db.relationship('GeneralLedgerEntry', backref='reconciliation_exceptions')
    
    def to_dict(self):
        """Convert reconciliation exception to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'bank_statement_id': self.bank_statement_id,
            'exception_type': self.exception_type,
            'exception_amount': float(self.exception_amount) if self.exception_amount else 0,
            'variance_amount': float(self.variance_amount) if self.variance_amount else None,
            'bank_transaction_id': self.bank_transaction_id,
            'system_transaction_id': self.system_transaction_id,
            'resolution_status': self.resolution_status,
            'resolution_notes': self.resolution_notes or '',
            'resolver_user_id': self.resolver_user_id,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'adjustment_ledger_entry_id': self.adjustment_ledger_entry_id,
            'created_by': self.created_by,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Machine learning training data
class ReconciliationTrainingData(db.Model):
    """ML training data from user decisions."""
    __tablename__ = 'reconciliation_training_data'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    
    # Input features
    bank_description = db.Column(db.Text, nullable=False)
    bank_amount = db.Column(db.Numeric(15, 2), nullable=False)
    receipt_vendor = db.Column(db.Text, nullable=True)
    receipt_amount = db.Column(db.Numeric(15, 2), nullable=True)
    date_difference = db.Column(db.Integer, nullable=True)  # days
    
    # Output (human decision)
    user_matched = db.Column(db.Boolean, nullable=False)
    match_confidence = db.Column(db.Numeric(5, 2), nullable=True)
    
    # Learning metadata
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    decision_timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Additional features for ML
    amount_difference = db.Column(db.Numeric(15, 2), nullable=True)
    description_similarity = db.Column(db.Numeric(5, 2), nullable=True)
    
    # Relationships
    organization = db.relationship('Organization', backref='training_data')
    user = db.relationship('User', backref='reconciliation_decisions')
    
    def to_dict(self):
        """Convert training data to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'bank_description': self.bank_description,
            'bank_amount': float(self.bank_amount) if self.bank_amount else 0,
            'receipt_vendor': self.receipt_vendor or '',
            'receipt_amount': float(self.receipt_amount) if self.receipt_amount else None,
            'date_difference': self.date_difference,
            'user_matched': self.user_matched,
            'match_confidence': float(self.match_confidence) if self.match_confidence else None,
            'user_id': self.user_id,
            'decision_timestamp': self.decision_timestamp.isoformat() if self.decision_timestamp else None,
            'amount_difference': float(self.amount_difference) if self.amount_difference else None,
            'description_similarity': float(self.description_similarity) if self.description_similarity else None
        }

# Enhanced matching configuration per organization
class SmartMatchingRules(db.Model):
    """Dynamic matching rules that learn from user behavior."""
    __tablename__ = 'smart_matching_rules'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    
    # Dynamic thresholds that learn from user behavior
    amount_tolerance_percent = db.Column(db.Numeric(5, 2), default=Decimal('2.00'))  # Start more lenient
    service_charge_tolerance = db.Column(db.Numeric(15, 2), default=Decimal('5.00'))  # Allow for fees
    date_tolerance_days = db.Column(db.Integer, default=3)  # Business reality
    description_threshold = db.Column(db.Numeric(5, 2), default=Decimal('0.70'))  # Lower for learning
    
    # Vendor-specific rules (learned from user decisions)
    vendor_patterns = db.Column(db.JSON, nullable=True)  # {"shell": {"typical_fee": 1.50}, "uber": {"fee_percent": 0.05}}
    
    # Auto-approval gets stricter as confidence improves
    auto_approve_threshold = db.Column(db.Numeric(5, 2), default=Decimal('85.00'))
    manual_review_threshold = db.Column(db.Numeric(5, 2), default=Decimal('50.00'))
    
    # Learning statistics
    total_decisions = db.Column(db.Integer, default=0)
    correct_auto_matches = db.Column(db.Integer, default=0)
    incorrect_auto_matches = db.Column(db.Integer, default=0)
    
    # ML configuration
    use_ml_predictions = db.Column(db.Boolean, default=False)
    ml_model_version = db.Column(db.String(50), nullable=True)
    ml_confidence_threshold = db.Column(db.Numeric(5, 2), default=Decimal('85.00'))
    
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='matching_rules')
    
    def update_from_decision(self, was_correct, confidence_used):
        """Update rules based on user feedback."""
        self.total_decisions += 1
        
        if was_correct:
            self.correct_auto_matches += 1
            # If we're getting most decisions right, we can be more aggressive
            success_rate = self.correct_auto_matches / self.total_decisions
            if success_rate > 0.95 and self.auto_approve_threshold < 95:
                self.auto_approve_threshold = min(95, self.auto_approve_threshold + 1)
        else:
            self.incorrect_auto_matches += 1
            # If we're making mistakes, be more conservative
            success_rate = self.correct_auto_matches / self.total_decisions
            if success_rate < 0.85 and self.auto_approve_threshold > 75:
                self.auto_approve_threshold = max(75, self.auto_approve_threshold - 2)
        
        self.last_updated = datetime.utcnow()
    
    def get_vendor_pattern(self, vendor_name):
        """Get learned patterns for a specific vendor."""
        if not self.vendor_patterns:
            return None
        
        vendor_key = vendor_name.lower().replace(' ', '_')
        return self.vendor_patterns.get(vendor_key)
    
    def update_vendor_pattern(self, vendor_name, pattern_data):
        """Update learned patterns for a vendor."""
        if not self.vendor_patterns:
            self.vendor_patterns = {}
        
        vendor_key = vendor_name.lower().replace(' ', '_')
        self.vendor_patterns[vendor_key] = pattern_data
        self.last_updated = datetime.utcnow()
    
    def to_dict(self):
        """Convert matching rules to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'amount_tolerance_percent': float(self.amount_tolerance_percent) if self.amount_tolerance_percent else 2.0,
            'service_charge_tolerance': float(self.service_charge_tolerance) if self.service_charge_tolerance else 5.0,
            'date_tolerance_days': self.date_tolerance_days,
            'description_threshold': float(self.description_threshold) if self.description_threshold else 0.7,
            'vendor_patterns': self.vendor_patterns or {},
            'auto_approve_threshold': float(self.auto_approve_threshold) if self.auto_approve_threshold else 85.0,
            'manual_review_threshold': float(self.manual_review_threshold) if self.manual_review_threshold else 50.0,
            'total_decisions': self.total_decisions,
            'correct_auto_matches': self.correct_auto_matches,
            'incorrect_auto_matches': self.incorrect_auto_matches,
            'success_rate': (self.correct_auto_matches / max(self.total_decisions, 1)) * 100,
            'use_ml_predictions': self.use_ml_predictions,
            'ml_model_version': self.ml_model_version or '',
            'ml_confidence_threshold': float(self.ml_confidence_threshold) if self.ml_confidence_threshold else 85.0,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }

# Split transaction handling
class SplitTransactionGroup(db.Model):
    """Groups of transactions that split into multiple receipts."""
    __tablename__ = 'split_transaction_group'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    
    # Group details
    group_type = db.Column(db.String(20), nullable=True)  # 'card_settlement', 'aggregated_payment', 'split_bill'
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    currency_code = db.Column(db.String(3), default='USD')
    
    # Source reference
    source_bank_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=True)
    
    # Status
    reconciliation_status = db.Column(db.String(20), default='pending')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='split_groups')
    source_transaction = db.relationship('FinancialTransaction', backref='split_groups')
    members = db.relationship('SplitTransactionMember', backref='split_group', lazy=True, cascade='all, delete-orphan')
    
    def get_variance(self):
        """Calculate variance between source amount and split total."""
        split_total = sum(member.amount for member in self.members)
        return self.total_amount - split_total
    
    def to_dict(self):
        """Convert split group to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'group_type': self.group_type or '',
            'total_amount': float(self.total_amount) if self.total_amount else 0,
            'currency_code': self.currency_code,
            'source_bank_transaction_id': self.source_bank_transaction_id,
            'reconciliation_status': self.reconciliation_status,
            'variance': float(self.get_variance()),
            'member_count': len(self.members),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'members': [member.to_dict() for member in self.members]
        }

class SplitTransactionMember(db.Model):
    """Individual members of a split transaction group."""
    __tablename__ = 'split_transaction_member'
    
    id = db.Column(db.Integer, primary_key=True)
    split_group_id = db.Column(db.Integer, db.ForeignKey('split_transaction_group.id'), nullable=False)
    financial_transaction_id = db.Column(db.Integer, db.ForeignKey('financial_transaction.id'), nullable=False)
    
    amount = db.Column(db.Numeric(15, 2), nullable=False)
    percentage = db.Column(db.Numeric(5, 2), nullable=True)  # Percentage of total split
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    financial_transaction = db.relationship('FinancialTransaction', backref='split_memberships')
    
    def to_dict(self):
        """Convert split member to dictionary."""
        return {
            'id': self.id,
            'split_group_id': self.split_group_id,
            'financial_transaction_id': self.financial_transaction_id,
            'amount': float(self.amount) if self.amount else 0,
            'percentage': float(self.percentage) if self.percentage else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Reconciliation audit log
class ReconciliationAudit(db.Model):
    """Comprehensive audit trail for reconciliation sessions."""
    __tablename__ = 'reconciliation_audit'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    bank_statement_id = db.Column(db.Integer, db.ForeignKey('bank_statement.id'), nullable=True)
    
    # Reconciliation session details
    reconciliation_date = db.Column(db.Date, nullable=False)
    reconciled_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Statistics
    total_transactions = db.Column(db.Integer, nullable=False)
    auto_matched = db.Column(db.Integer, nullable=False)
    manual_matched = db.Column(db.Integer, nullable=False)
    unmatched = db.Column(db.Integer, nullable=False)
    exceptions_created = db.Column(db.Integer, default=0)
    
    # Balance reconciliation
    bank_opening_balance = db.Column(db.Numeric(15, 2), nullable=True)
    bank_closing_balance = db.Column(db.Numeric(15, 2), nullable=True)
    system_opening_balance = db.Column(db.Numeric(15, 2), nullable=True)
    system_closing_balance = db.Column(db.Numeric(15, 2), nullable=True)
    variance = db.Column(db.Numeric(15, 2), nullable=True)
    variance_explained = db.Column(db.Boolean, default=False)
    variance_notes = db.Column(db.Text, nullable=True)
    
    # Timing
    session_start_time = db.Column(db.DateTime, nullable=False)
    session_end_time = db.Column(db.DateTime, nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='reconciliation_audits')
    bank_statement = db.relationship('BankStatement', backref='reconciliation_audits')
    reconciled_by_user = db.relationship('User', backref='reconciliation_audits')
    
    def get_session_duration(self):
        """Calculate session duration in minutes."""
        if not self.session_end_time:
            return None
        delta = self.session_end_time - self.session_start_time
        return round(delta.total_seconds() / 60, 2)
    
    def get_auto_match_rate(self):
        """Calculate auto-match success rate."""
        if self.total_transactions == 0:
            return 0
        return round((self.auto_matched / self.total_transactions) * 100, 2)
    
    def to_dict(self):
        """Convert reconciliation audit to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'bank_statement_id': self.bank_statement_id,
            'reconciliation_date': self.reconciliation_date.isoformat() if self.reconciliation_date else None,
            'reconciled_by': self.reconciled_by,
            'total_transactions': self.total_transactions,
            'auto_matched': self.auto_matched,
            'manual_matched': self.manual_matched,
            'unmatched': self.unmatched,
            'exceptions_created': self.exceptions_created,
            'bank_opening_balance': float(self.bank_opening_balance) if self.bank_opening_balance else None,
            'bank_closing_balance': float(self.bank_closing_balance) if self.bank_closing_balance else None,
            'system_opening_balance': float(self.system_opening_balance) if self.system_opening_balance else None,
            'system_closing_balance': float(self.system_closing_balance) if self.system_closing_balance else None,
            'variance': float(self.variance) if self.variance else None,
            'variance_explained': self.variance_explained,
            'variance_notes': self.variance_notes or '',
            'session_start_time': self.session_start_time.isoformat() if self.session_start_time else None,
            'session_end_time': self.session_end_time.isoformat() if self.session_end_time else None,
            'session_duration_minutes': self.get_session_duration(),
            'auto_match_rate': self.get_auto_match_rate(),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

# Performance optimization - cached account balances
class AccountBalanceCache(db.Model):
    """Materialized account balances for performance."""
    __tablename__ = 'account_balance_cache'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    
    # Balance by period
    balance_as_of_date = db.Column(db.Date, nullable=False)
    debit_balance = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    credit_balance = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    net_balance = db.Column(db.Numeric(15, 2), nullable=True)  # Calculated based on account type
    
    # Multi-currency support
    base_currency_balance = db.Column(db.Numeric(15, 2), nullable=True)  # Converted to org base currency
    
    # Cache metadata
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='balance_cache')
    account = db.relationship('Account', backref='balance_cache')
    
    # Unique constraint
    __table_args__ = (db.UniqueConstraint('organization_id', 'account_id', 'balance_as_of_date'),)
    
    def to_dict(self):
        """Convert balance cache to dictionary."""
        return {
            'id': self.id,
            'organization_id': self.organization_id,
            'account_id': self.account_id,
            'balance_as_of_date': self.balance_as_of_date.isoformat() if self.balance_as_of_date else None,
            'debit_balance': float(self.debit_balance) if self.debit_balance else 0,
            'credit_balance': float(self.credit_balance) if self.credit_balance else 0,
            'net_balance': float(self.net_balance) if self.net_balance else 0,
            'base_currency_balance': float(self.base_currency_balance) if self.base_currency_balance else 0,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None
        }