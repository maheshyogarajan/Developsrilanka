"""
Accounting models for the comprehensive accounting system.
These models extend the existing application with full accounting capabilities.
"""

from app import db
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import func
from enum import Enum

class AccountType(Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"

class DepreciationMethod(Enum):
    STRAIGHT_LINE = "straight_line"
    DECLINING_BALANCE = "declining_balance"
    UNITS_OF_PRODUCTION = "units_of_production"

class AssetStatus(Enum):
    ACTIVE = "active"
    DISPOSED = "disposed"
    SOLD = "sold"
    RETIRED = "retired"

class Account(db.Model):
    """Chart of accounts model for organization-specific accounting."""
    __tablename__ = 'account'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    account_code = db.Column(db.String(20), nullable=False)
    account_name = db.Column(db.String(255), nullable=False)
    account_type = db.Column(db.String(50), nullable=False)
    account_subtype = db.Column(db.String(50), nullable=True)
    parent_account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='accounts')
    parent_account = db.relationship('Account', remote_side=[id], backref='child_accounts')
    journal_lines = db.relationship('JournalEntryLine', backref='account', lazy=True)
    
    # Unique constraint
    __table_args__ = (db.UniqueConstraint('organization_id', 'account_code'),)
    
    def __repr__(self):
        return f'<Account {self.account_code}: {self.account_name}>'
    
    def get_balance(self, as_of_date=None):
        """Calculate account balance as of a specific date."""
        if not as_of_date:
            as_of_date = date.today()
        
        # Get all journal lines for this account up to the date
        query = db.session.query(
            func.sum(JournalEntryLine.debit_amount).label('total_debits'),
            func.sum(JournalEntryLine.credit_amount).label('total_credits')
        ).join(GeneralLedgerEntry).filter(
            JournalEntryLine.account_id == self.id,
            GeneralLedgerEntry.transaction_date <= as_of_date
        )
        
        result = query.first()
        total_debits = result.total_debits or Decimal('0')
        total_credits = result.total_credits or Decimal('0')
        
        # Balance calculation depends on account type
        if self.account_type in ['asset', 'expense']:
            # Normal debit balance accounts
            return total_debits - total_credits
        else:
            # Normal credit balance accounts (liability, equity, revenue)
            return total_credits - total_debits
    
    def get_children_recursive(self):
        """Get all child accounts recursively."""
        children = []
        for child in self.child_accounts:
            children.append(child)
            children.extend(child.get_children_recursive())
        return children
    
    def to_dict(self):
        """Convert account to dictionary."""
        return {
            'id': self.id,
            'account_code': self.account_code,
            'account_name': self.account_name,
            'account_type': self.account_type,
            'account_subtype': self.account_subtype,
            'parent_account_id': self.parent_account_id,
            'is_active': self.is_active,
            'balance': float(self.get_balance()),
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class GeneralLedgerEntry(db.Model):
    """General ledger entries for double-entry bookkeeping."""
    __tablename__ = 'general_ledger_entry'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    entry_number = db.Column(db.String(50), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    reference_type = db.Column(db.String(50), nullable=True)  # 'receipt', 'invoice', 'manual', 'depreciation'
    reference_id = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=False)
    total_amount = db.Column(db.Numeric(15, 2), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='ledger_entries')
    created_by_user = db.relationship('User', backref='created_ledger_entries')
    journal_lines = db.relationship('JournalEntryLine', backref='ledger_entry', lazy=True, cascade='all, delete-orphan')
    
    # Unique constraint
    __table_args__ = (db.UniqueConstraint('organization_id', 'entry_number'),)
    
    def __repr__(self):
        return f'<LedgerEntry {self.entry_number}: {self.description}>'
    
    def is_balanced(self):
        """Check if the journal entry is balanced (debits = credits)."""
        total_debits = sum(line.debit_amount for line in self.journal_lines)
        total_credits = sum(line.credit_amount for line in self.journal_lines)
        return abs(total_debits - total_credits) < Decimal('0.01')
    
    def to_dict(self):
        """Convert ledger entry to dictionary."""
        return {
            'id': self.id,
            'entry_number': self.entry_number,
            'transaction_date': self.transaction_date.isoformat(),
            'reference_type': self.reference_type,
            'reference_id': self.reference_id,
            'description': self.description,
            'total_amount': float(self.total_amount),
            'created_at': self.created_at.isoformat(),
            'journal_lines': [line.to_dict() for line in self.journal_lines],
            'is_balanced': self.is_balanced()
        }

class JournalEntryLine(db.Model):
    """Individual lines within a journal entry."""
    __tablename__ = 'journal_entry_line'
    
    id = db.Column(db.Integer, primary_key=True)
    ledger_entry_id = db.Column(db.Integer, db.ForeignKey('general_ledger_entry.id'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'), nullable=False)
    debit_amount = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    credit_amount = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    description = db.Column(db.Text, nullable=True)
    line_number = db.Column(db.Integer, nullable=False)
    
    def __repr__(self):
        return f'<JournalLine {self.line_number}: Dr {self.debit_amount}, Cr {self.credit_amount}>'
    
    def to_dict(self):
        """Convert journal line to dictionary."""
        return {
            'id': self.id,
            'account_id': self.account_id,
            'account_code': self.account.account_code if self.account else None,
            'account_name': self.account.account_name if self.account else None,
            'debit_amount': float(self.debit_amount),
            'credit_amount': float(self.credit_amount),
            'description': self.description,
            'line_number': self.line_number
        }

class AssetCategory(db.Model):
    """Asset categories for depreciation calculations."""
    __tablename__ = 'asset_category'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    depreciation_method = db.Column(db.String(50), default='straight_line')
    useful_life_years = db.Column(db.Integer, default=5)
    salvage_value_percentage = db.Column(db.Numeric(5, 2), default=Decimal('0'))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='asset_categories')
    assets = db.relationship('FixedAsset', backref='category', lazy=True)
    
    def __repr__(self):
        return f'<AssetCategory {self.name}>'
    
    def to_dict(self):
        """Convert asset category to dictionary."""
        return {
            'id': self.id,
            'name': self.name,
            'depreciation_method': self.depreciation_method,
            'useful_life_years': self.useful_life_years,
            'salvage_value_percentage': float(self.salvage_value_percentage),
            'is_active': self.is_active
        }

class FixedAsset(db.Model):
    """Fixed assets register with depreciation tracking."""
    __tablename__ = 'fixed_asset'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    asset_category_id = db.Column(db.Integer, db.ForeignKey('asset_category.id'), nullable=True)
    asset_number = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    purchase_price = db.Column(db.Numeric(15, 2), nullable=False)
    useful_life_years = db.Column(db.Integer, nullable=True)
    salvage_value = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    current_book_value = db.Column(db.Numeric(15, 2), nullable=True)
    accumulated_depreciation = db.Column(db.Numeric(15, 2), default=Decimal('0'))
    status = db.Column(db.String(50), default='active')
    location = db.Column(db.Text, nullable=True)
    purchase_receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='fixed_assets')
    purchase_receipt = db.relationship('Receipt', backref='purchased_assets')
    depreciation_entries = db.relationship('DepreciationEntry', backref='asset', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<FixedAsset {self.asset_number}: {self.description}>'
    
    def calculate_depreciation(self, period_date):
        """Calculate depreciation for a specific period."""
        if self.status != 'active':
            return Decimal('0')
        
        # Use category defaults if not specified
        useful_life = self.useful_life_years or (self.category.useful_life_years if self.category else 5)
        salvage_percentage = self.category.salvage_value_percentage if self.category else Decimal('0')
        salvage_value = self.purchase_price * (salvage_percentage / 100)
        
        depreciable_amount = self.purchase_price - salvage_value
        
        # Calculate based on depreciation method
        method = self.category.depreciation_method if self.category else 'straight_line'
        
        if method == 'straight_line':
            annual_depreciation = depreciable_amount / useful_life
            monthly_depreciation = annual_depreciation / 12
            return monthly_depreciation
        
        # Add other depreciation methods as needed
        return Decimal('0')
    
    def get_current_book_value(self):
        """Calculate current book value."""
        return self.purchase_price - self.accumulated_depreciation
    
    def to_dict(self):
        """Convert fixed asset to dictionary."""
        return {
            'id': self.id,
            'asset_number': self.asset_number,
            'description': self.description,
            'purchase_date': self.purchase_date.isoformat(),
            'purchase_price': float(self.purchase_price),
            'useful_life_years': self.useful_life_years,
            'salvage_value': float(self.salvage_value),
            'accumulated_depreciation': float(self.accumulated_depreciation),
            'current_book_value': float(self.get_current_book_value()),
            'status': self.status,
            'location': self.location,
            'category_name': self.category.name if self.category else None,
            'created_at': self.created_at.isoformat()
        }

class DepreciationEntry(db.Model):
    """Depreciation schedule entries."""
    __tablename__ = 'depreciation_entry'
    
    id = db.Column(db.Integer, primary_key=True)
    fixed_asset_id = db.Column(db.Integer, db.ForeignKey('fixed_asset.id'), nullable=False)
    period_date = db.Column(db.Date, nullable=False)
    depreciation_amount = db.Column(db.Numeric(15, 2), nullable=False)
    accumulated_depreciation = db.Column(db.Numeric(15, 2), nullable=False)
    book_value = db.Column(db.Numeric(15, 2), nullable=False)
    ledger_entry_id = db.Column(db.Integer, db.ForeignKey('general_ledger_entry.id'), nullable=True)
    is_calculated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    ledger_entry = db.relationship('GeneralLedgerEntry', backref='depreciation_entries')
    
    def __repr__(self):
        return f'<DepreciationEntry {self.period_date}: {self.depreciation_amount}>'
    
    def to_dict(self):
        """Convert depreciation entry to dictionary."""
        return {
            'id': self.id,
            'period_date': self.period_date.isoformat(),
            'depreciation_amount': float(self.depreciation_amount),
            'accumulated_depreciation': float(self.accumulated_depreciation),
            'book_value': float(self.book_value),
            'is_calculated': self.is_calculated,
            'created_at': self.created_at.isoformat()
        }

class AccountingPeriod(db.Model):
    """Accounting periods for financial reporting."""
    __tablename__ = 'accounting_period'
    
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    period_name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default='open')  # 'open', 'closed'
    closed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    organization = db.relationship('Organization', backref='accounting_periods')
    closed_by_user = db.relationship('User', backref='closed_periods')
    
    def __repr__(self):
        return f'<AccountingPeriod {self.period_name}>'
    
    def to_dict(self):
        """Convert accounting period to dictionary."""
        return {
            'id': self.id,
            'period_name': self.period_name,
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'status': self.status,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'created_at': self.created_at.isoformat()
        }