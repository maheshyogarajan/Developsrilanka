"""
Create accounting tables for the comprehensive accounting system.
This script creates all necessary tables for chart of accounts, general ledger,
assets, and depreciation tracking.
"""

import os
from sqlalchemy import create_engine, text
from app import app, db

def create_accounting_tables():
    """Create all accounting-related tables."""
    
    with app.app_context():
        try:
            # Chart of Accounts
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS account (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    account_code VARCHAR(20) NOT NULL,
                    account_name VARCHAR(255) NOT NULL,
                    account_type VARCHAR(50) NOT NULL,
                    account_subtype VARCHAR(50),
                    parent_account_id INTEGER REFERENCES account(id),
                    is_active BOOLEAN DEFAULT TRUE,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(organization_id, account_code)
                );
            """))
            
            # General Ledger Entries
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS general_ledger_entry (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    entry_number VARCHAR(50) NOT NULL,
                    transaction_date DATE NOT NULL,
                    reference_type VARCHAR(50),
                    reference_id INTEGER,
                    description TEXT NOT NULL,
                    total_amount DECIMAL(15,2) NOT NULL,
                    created_by INTEGER REFERENCES "user"(id),
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(organization_id, entry_number)
                );
            """))
            
            # Journal Entry Lines
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS journal_entry_line (
                    id SERIAL PRIMARY KEY,
                    ledger_entry_id INTEGER REFERENCES general_ledger_entry(id) NOT NULL,
                    account_id INTEGER REFERENCES account(id) NOT NULL,
                    debit_amount DECIMAL(15,2) DEFAULT 0,
                    credit_amount DECIMAL(15,2) DEFAULT 0,
                    description TEXT,
                    line_number INTEGER NOT NULL
                );
            """))
            
            # Asset Categories
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS asset_category (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    depreciation_method VARCHAR(50) DEFAULT 'straight_line',
                    useful_life_years INTEGER DEFAULT 5,
                    salvage_value_percentage DECIMAL(5,2) DEFAULT 0,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            # Fixed Assets
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS fixed_asset (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    asset_category_id INTEGER REFERENCES asset_category(id),
                    asset_number VARCHAR(100),
                    description TEXT NOT NULL,
                    purchase_date DATE NOT NULL,
                    purchase_price DECIMAL(15,2) NOT NULL,
                    useful_life_years INTEGER,
                    salvage_value DECIMAL(15,2) DEFAULT 0,
                    current_book_value DECIMAL(15,2),
                    accumulated_depreciation DECIMAL(15,2) DEFAULT 0,
                    status VARCHAR(50) DEFAULT 'active',
                    location TEXT,
                    purchase_receipt_id INTEGER REFERENCES receipt(id),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            # Depreciation Entries
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS depreciation_entry (
                    id SERIAL PRIMARY KEY,
                    fixed_asset_id INTEGER REFERENCES fixed_asset(id) NOT NULL,
                    period_date DATE NOT NULL,
                    depreciation_amount DECIMAL(15,2) NOT NULL,
                    accumulated_depreciation DECIMAL(15,2) NOT NULL,
                    book_value DECIMAL(15,2) NOT NULL,
                    ledger_entry_id INTEGER REFERENCES general_ledger_entry(id),
                    is_calculated BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            # Accounting Periods
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS accounting_period (
                    id SERIAL PRIMARY KEY,
                    organization_id INTEGER REFERENCES organization(id) NOT NULL,
                    period_name VARCHAR(100) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NOT NULL,
                    status VARCHAR(50) DEFAULT 'open',
                    closed_by INTEGER REFERENCES "user"(id),
                    closed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))
            
            # Create indexes for performance
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_account_org_type ON account(organization_id, account_type);"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_ledger_org_date ON general_ledger_entry(organization_id, transaction_date);"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_journal_line_account ON journal_entry_line(account_id);"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_asset_org_status ON fixed_asset(organization_id, status);"))
            db.session.execute(text("CREATE INDEX IF NOT EXISTS idx_depreciation_asset_date ON depreciation_entry(fixed_asset_id, period_date);"))
            
            db.session.commit()
            print("✅ All accounting tables created successfully!")
            
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error creating accounting tables: {str(e)}")
            raise

def create_default_chart_of_accounts(organization_id):
    """Create default chart of accounts for an organization."""
    
    default_accounts = [
        # Assets (1000-1999)
        {'code': '1000', 'name': 'Current Assets', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1100', 'name': 'Cash and Cash Equivalents', 'type': 'asset', 'subtype': 'current_asset', 'parent': '1000'},
        {'code': '1200', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current_asset', 'parent': '1000'},
        {'code': '1300', 'name': 'Inventory', 'type': 'asset', 'subtype': 'current_asset', 'parent': '1000'},
        {'code': '1400', 'name': 'Prepaid Expenses', 'type': 'asset', 'subtype': 'current_asset', 'parent': '1000'},
        
        {'code': '1500', 'name': 'Fixed Assets', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1510', 'name': 'Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset', 'parent': '1500'},
        {'code': '1520', 'name': 'Computer Equipment', 'type': 'asset', 'subtype': 'fixed_asset', 'parent': '1500'},
        {'code': '1530', 'name': 'Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset', 'parent': '1500'},
        {'code': '1540', 'name': 'Vehicles', 'type': 'asset', 'subtype': 'fixed_asset', 'parent': '1500'},
        {'code': '1550', 'name': 'Accumulated Depreciation', 'type': 'asset', 'subtype': 'accumulated_depreciation', 'parent': '1500'},
        
        # Liabilities (2000-2999)
        {'code': '2000', 'name': 'Current Liabilities', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2100', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current_liability', 'parent': '2000'},
        {'code': '2200', 'name': 'Accrued Expenses', 'type': 'liability', 'subtype': 'current_liability', 'parent': '2000'},
        {'code': '2300', 'name': 'Short-term Loans', 'type': 'liability', 'subtype': 'current_liability', 'parent': '2000'},
        
        {'code': '2500', 'name': 'Long-term Liabilities', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2510', 'name': 'Long-term Loans', 'type': 'liability', 'subtype': 'long_term_liability', 'parent': '2500'},
        
        # Equity (3000-3999)
        {'code': '3000', 'name': 'Equity', 'type': 'equity', 'subtype': 'equity'},
        {'code': '3100', 'name': 'Owner\'s Equity', 'type': 'equity', 'subtype': 'equity', 'parent': '3000'},
        {'code': '3200', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'equity', 'parent': '3000'},
        
        # Revenue (4000-4999)
        {'code': '4000', 'name': 'Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4100', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue', 'parent': '4000'},
        {'code': '4200', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue', 'parent': '4000'},
        {'code': '4300', 'name': 'Other Revenue', 'type': 'revenue', 'subtype': 'other_revenue', 'parent': '4000'},
        
        # Expenses (5000-5999)
        {'code': '5000', 'name': 'Operating Expenses', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5100', 'name': 'Office Expenses', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5200', 'name': 'Travel & Entertainment', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5300', 'name': 'Professional Services', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5400', 'name': 'Utilities', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5500', 'name': 'Marketing & Advertising', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5600', 'name': 'Rent Expense', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5700', 'name': 'Insurance', 'type': 'expense', 'subtype': 'operating_expense', 'parent': '5000'},
        {'code': '5800', 'name': 'Depreciation Expense', 'type': 'expense', 'subtype': 'depreciation_expense', 'parent': '5000'},
        {'code': '5900', 'name': 'Other Expenses', 'type': 'expense', 'subtype': 'other_expense', 'parent': '5000'},
    ]
    
    try:
        # Get parent account IDs
        parent_accounts = {}
        
        for account_data in default_accounts:
            if 'parent' not in account_data:
                # Create parent account first
                db.session.execute(text("""
                    INSERT INTO account (organization_id, account_code, account_name, account_type, account_subtype)
                    VALUES (:org_id, :code, :name, :type, :subtype)
                    ON CONFLICT (organization_id, account_code) DO NOTHING
                """), {
                    'org_id': organization_id,
                    'code': account_data['code'],
                    'name': account_data['name'],
                    'type': account_data['type'],
                    'subtype': account_data['subtype']
                })
                
                # Get the account ID
                result = db.session.execute(text("""
                    SELECT id FROM account WHERE organization_id = :org_id AND account_code = :code
                """), {'org_id': organization_id, 'code': account_data['code']}).fetchone()
                
                if result:
                    parent_accounts[account_data['code']] = result[0]
        
        # Create child accounts
        for account_data in default_accounts:
            if 'parent' in account_data:
                parent_id = parent_accounts.get(account_data['parent'])
                
                db.session.execute(text("""
                    INSERT INTO account (organization_id, account_code, account_name, account_type, account_subtype, parent_account_id)
                    VALUES (:org_id, :code, :name, :type, :subtype, :parent_id)
                    ON CONFLICT (organization_id, account_code) DO NOTHING
                """), {
                    'org_id': organization_id,
                    'code': account_data['code'],
                    'name': account_data['name'],
                    'type': account_data['type'],
                    'subtype': account_data['subtype'],
                    'parent_id': parent_id
                })
        
        db.session.commit()
        print(f"✅ Default chart of accounts created for organization {organization_id}")
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error creating default chart of accounts: {str(e)}")
        raise

if __name__ == "__main__":
    create_accounting_tables()