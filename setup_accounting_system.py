"""
Comprehensive accounting system setup script.
This script will:
1. Create essential chart of accounts for all existing organizations
2. Link existing invoices and expenses to proper accounting entries
3. Generate historical journal entries from transaction data
4. Set up default asset categories
"""

import os
import sys
from datetime import datetime, date
from decimal import Decimal

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Organization, User, Receipt, Invoice, Client, CompanyExpense
from accounting_models import (
    Account, GeneralLedgerEntry, JournalEntryLine, AssetCategory, 
    AccountType, DepreciationMethod
)
from accounting_service import AccountingService

def create_standard_chart_of_accounts(organization_id):
    """Create standard chart of accounts for an organization."""
    print(f"Creating chart of accounts for organization {organization_id}...")
    
    accounts_to_create = [
        # Assets (1000-1999)
        {'code': '1000', 'name': 'Cash and Cash Equivalents', 'type': 'asset', 'subtype': 'current'},
        {'code': '1100', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current'},
        {'code': '1200', 'name': 'Prepaid Expenses', 'type': 'asset', 'subtype': 'current'},
        {'code': '1500', 'name': 'Equipment', 'type': 'asset', 'subtype': 'fixed'},
        {'code': '1510', 'name': 'Accumulated Depreciation - Equipment', 'type': 'asset', 'subtype': 'contra'},
        {'code': '1600', 'name': 'Furniture and Fixtures', 'type': 'asset', 'subtype': 'fixed'},
        {'code': '1610', 'name': 'Accumulated Depreciation - Furniture', 'type': 'asset', 'subtype': 'contra'},
        
        # Liabilities (2000-2999)
        {'code': '2000', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current'},
        {'code': '2100', 'name': 'Accrued Expenses', 'type': 'liability', 'subtype': 'current'},
        {'code': '2200', 'name': 'Short-term Loans', 'type': 'liability', 'subtype': 'current'},
        {'code': '2500', 'name': 'Long-term Debt', 'type': 'liability', 'subtype': 'long_term'},
        
        # Equity (3000-3999)
        {'code': '3000', 'name': "Owner's Equity", 'type': 'equity', 'subtype': 'capital'},
        {'code': '3100', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'earnings'},
        
        # Revenue (4000-4999)
        {'code': '4000', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating'},
        {'code': '4100', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating'},
        {'code': '4200', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating'},
        {'code': '4900', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'non_operating'},
        
        # Expenses (5000-5999)
        {'code': '5000', 'name': 'Office Supplies', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5100', 'name': 'Travel and Transportation', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5200', 'name': 'Professional Services', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5300', 'name': 'Marketing and Advertising', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5400', 'name': 'Utilities', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5500', 'name': 'Rent Expense', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5600', 'name': 'Insurance', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5700', 'name': 'Telecommunications', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5800', 'name': 'Meals and Entertainment', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5850', 'name': 'Depreciation Expense', 'type': 'expense', 'subtype': 'operating'},
        {'code': '5900', 'name': 'Miscellaneous Expenses', 'type': 'expense', 'subtype': 'operating'},
    ]
    
    created_accounts = {}
    
    for account_data in accounts_to_create:
        # Check if account already exists
        existing = Account.query.filter_by(
            organization_id=organization_id,
            account_code=account_data['code']
        ).first()
        
        if not existing:
            account = Account(
                organization_id=organization_id,
                account_code=account_data['code'],
                account_name=account_data['name'],
                account_type=account_data['type'],
                account_subtype=account_data['subtype'],
                is_active=True,
                description=f"Standard {account_data['type']} account"
            )
            db.session.add(account)
            created_accounts[account_data['code']] = account
            print(f"  Created account: {account_data['code']} - {account_data['name']}")
        else:
            created_accounts[account_data['code']] = existing
            print(f"  Account already exists: {account_data['code']} - {account_data['name']}")
    
    db.session.commit()
    return created_accounts

def create_default_asset_categories(organization_id):
    """Create default asset categories for depreciation."""
    print(f"Creating asset categories for organization {organization_id}...")
    
    categories = [
        {'name': 'Computer Equipment', 'useful_life': 3, 'method': 'straight_line'},
        {'name': 'Office Furniture', 'useful_life': 7, 'method': 'straight_line'},
        {'name': 'Vehicles', 'useful_life': 5, 'method': 'straight_line'},
        {'name': 'Machinery', 'useful_life': 10, 'method': 'straight_line'},
        {'name': 'Software', 'useful_life': 3, 'method': 'straight_line'},
    ]
    
    for cat_data in categories:
        existing = AssetCategory.query.filter_by(
            organization_id=organization_id,
            name=cat_data['name']
        ).first()
        
        if not existing:
            category = AssetCategory(
                organization_id=organization_id,
                name=cat_data['name'],
                depreciation_method=cat_data['method'],
                useful_life_years=cat_data['useful_life'],
                salvage_value_percentage=Decimal('0'),
                is_active=True
            )
            db.session.add(category)
            print(f"  Created asset category: {cat_data['name']}")
    
    db.session.commit()

def migrate_existing_invoices(organization_id, accounts):
    """Create journal entries for existing invoices."""
    print(f"Migrating existing invoices for organization {organization_id}...")
    
    # Get existing invoices for this organization
    invoices = Invoice.query.filter_by(organization_id=organization_id).all()
    
    # Get relevant accounts
    accounts_receivable = accounts.get('1100')  # Accounts Receivable
    service_revenue = accounts.get('4000')      # Service Revenue
    
    if not accounts_receivable or not service_revenue:
        print("  Warning: Required accounts not found for invoice migration")
        return
    
    migrated_count = 0
    
    for invoice in invoices:
        # Check if journal entry already exists
        existing_entry = GeneralLedgerEntry.query.filter_by(
            organization_id=organization_id,
            reference_type='invoice',
            reference_id=invoice.id
        ).first()
        
        if existing_entry:
            continue
        
        # Create journal entry for invoice
        entry_number = AccountingService.get_next_entry_number(organization_id)
        
        journal_entry = GeneralLedgerEntry(
            organization_id=organization_id,
            entry_number=entry_number,
            transaction_date=invoice.created_at.date(),
            reference_type='invoice',
            reference_id=invoice.id,
            description=f"Invoice #{invoice.invoice_number} - {invoice.client.name if invoice.client else 'Unknown Client'}",
            total_amount=invoice.total,
            created_by=invoice.user_id
        )
        db.session.add(journal_entry)
        db.session.flush()  # Get the ID
        
        # Debit Accounts Receivable
        debit_line = JournalEntryLine(
            ledger_entry_id=journal_entry.id,
            account_id=accounts_receivable.id,
            debit_amount=invoice.total,
            credit_amount=Decimal('0'),
            description=f"Invoice #{invoice.invoice_number}",
            line_number=1
        )
        db.session.add(debit_line)
        
        # Credit Service Revenue
        credit_line = JournalEntryLine(
            ledger_entry_id=journal_entry.id,
            account_id=service_revenue.id,
            debit_amount=Decimal('0'),
            credit_amount=invoice.total,
            description=f"Invoice #{invoice.invoice_number}",
            line_number=2
        )
        db.session.add(credit_line)
        
        migrated_count += 1
    
    db.session.commit()
    print(f"  Migrated {migrated_count} invoices to journal entries")

def migrate_existing_expenses(organization_id, accounts):
    """Create journal entries for existing expenses/receipts."""
    print(f"Migrating existing expenses for organization {organization_id}...")
    
    # Get existing receipts for this organization
    receipts = Receipt.query.filter_by(organization_id=organization_id).all()
    
    # Get relevant accounts
    cash_account = accounts.get('1000')          # Cash
    office_supplies = accounts.get('5000')       # Office Supplies
    travel_expense = accounts.get('5100')        # Travel
    professional_services = accounts.get('5200') # Professional Services
    misc_expense = accounts.get('5900')          # Miscellaneous
    
    if not cash_account:
        print("  Warning: Cash account not found for expense migration")
        return
    
    migrated_count = 0
    
    for receipt in receipts:
        # Skip if already migrated
        existing_entry = GeneralLedgerEntry.query.filter_by(
            organization_id=organization_id,
            reference_type='receipt',
            reference_id=receipt.id
        ).first()
        
        if existing_entry:
            continue
        
        # Determine expense account based on receipt data
        expense_account = misc_expense  # Default
        
        if receipt.category:
            category_lower = receipt.category.lower()
            if 'office' in category_lower or 'supply' in category_lower:
                expense_account = office_supplies
            elif 'travel' in category_lower or 'transport' in category_lower:
                expense_account = travel_expense
            elif 'professional' in category_lower or 'service' in category_lower:
                expense_account = professional_services
        
        if not expense_account:
            expense_account = misc_expense
        
        # Create journal entry
        entry_number = AccountingService.get_next_entry_number(organization_id)
        
        journal_entry = GeneralLedgerEntry(
            organization_id=organization_id,
            entry_number=entry_number,
            transaction_date=receipt.created_at.date(),
            reference_type='receipt',
            reference_id=receipt.id,
            description=f"Receipt: {receipt.vendor_name or 'Unknown Vendor'} - {receipt.description or 'Expense'}",
            total_amount=receipt.total_amount,
            created_by=receipt.user_id
        )
        db.session.add(journal_entry)
        db.session.flush()
        
        # Debit Expense Account
        debit_line = JournalEntryLine(
            ledger_entry_id=journal_entry.id,
            account_id=expense_account.id,
            debit_amount=receipt.total_amount,
            credit_amount=Decimal('0'),
            description=f"Receipt from {receipt.vendor_name or 'Unknown'}",
            line_number=1
        )
        db.session.add(debit_line)
        
        # Credit Cash Account
        credit_line = JournalEntryLine(
            ledger_entry_id=journal_entry.id,
            account_id=cash_account.id,
            debit_amount=Decimal('0'),
            credit_amount=receipt.total_amount,
            description=f"Payment for receipt",
            line_number=2
        )
        db.session.add(credit_line)
        
        migrated_count += 1
    
    db.session.commit()
    print(f"  Migrated {migrated_count} receipts to journal entries")

def setup_organization_accounting(org_id):
    """Set up complete accounting system for one organization."""
    print(f"\n=== Setting up accounting for organization {org_id} ===")
    
    org = Organization.query.get(org_id)
    if not org:
        print(f"Organization {org_id} not found")
        return
    
    print(f"Organization: {org.name}")
    
    # Create chart of accounts
    accounts = create_standard_chart_of_accounts(org_id)
    
    # Create asset categories
    create_default_asset_categories(org_id)
    
    # Migrate existing data
    migrate_existing_invoices(org_id, accounts)
    migrate_existing_expenses(org_id, accounts)
    
    print(f"✓ Accounting setup complete for {org.name}")

def main():
    """Main setup function."""
    print("🚀 Starting comprehensive accounting system setup...")
    print("This will create chart of accounts and migrate existing data for all organizations.")
    
    with app.app_context():
        try:
            # Get all organizations
            organizations = Organization.query.all()
            print(f"\nFound {len(organizations)} organizations to set up:")
            
            for org in organizations:
                print(f"  - {org.name} (ID: {org.id})")
            
            # Set up accounting for each organization
            for org in organizations:
                try:
                    setup_organization_accounting(org.id)
                except Exception as e:
                    print(f"❌ Error setting up accounting for {org.name}: {str(e)}")
                    continue
            
            print("\n🎉 Accounting system setup completed successfully!")
            print("\nWhat was created:")
            print("✓ Standard chart of accounts for each organization")
            print("✓ Default asset categories for depreciation tracking")
            print("✓ Journal entries for existing invoices and receipts")
            print("✓ Proper double-entry bookkeeping structure")
            
            print("\nYou can now:")
            print("• View financial reports in the Accounts section")
            print("• Track assets and depreciation")
            print("• Generate profit & loss statements")
            print("• Monitor journal entries and account balances")
            
        except Exception as e:
            print(f"❌ Setup failed: {str(e)}")
            return False
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)