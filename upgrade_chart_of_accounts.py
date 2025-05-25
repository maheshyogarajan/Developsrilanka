"""
Upgrade existing organizations to the comprehensive 74-account chart of accounts.
"""
import sys
import os
sys.path.insert(0, os.getcwd())

from main import app
from models import db, Organization
from accounting_models import Account
from datetime import datetime

def get_comprehensive_accounts():
    """Get the complete 74-account chart of accounts."""
    
    return [
        # ASSETS (23 accounts)
        # Cash & Equivalents
        {'code': '1001', 'name': 'Cash – Primary Bank', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1002', 'name': 'Cash – Business Savings', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1005', 'name': 'Petty Cash', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1050', 'name': 'Short-Term Investments', 'type': 'asset', 'subtype': 'current_asset'},
        
        # Receivables & Other Current Assets
        {'code': '1100', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1110', 'name': 'Allowance for Doubtful Accounts', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1200', 'name': 'Inventory', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1300', 'name': 'Prepaid Expenses', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1350', 'name': 'Input VAT / GST Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1400', 'name': 'Security Deposits & Advances', 'type': 'asset', 'subtype': 'current_asset'},
        
        # Fixed Assets – Tangible
        {'code': '1500', 'name': 'Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1550', 'name': 'Accum. Depreciation – Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1600', 'name': 'Computer Equipment & Software', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1650', 'name': 'Accum. Depreciation – Computer Equipment & Software', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1700', 'name': 'Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1750', 'name': 'Accum. Depreciation – Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1800', 'name': 'Buildings / Leasehold Improvements', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1850', 'name': 'Accum. Depreciation – Buildings & Leasehold Improvements', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1900', 'name': 'Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1950', 'name': 'Accum. Depreciation – Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        
        # Fixed Assets – Intangible
        {'code': '1970', 'name': 'Intangible Assets (Licences, IP)', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1980', 'name': 'Accum. Amortisation – Intangibles', 'type': 'asset', 'subtype': 'fixed_asset'},
        
        # LIABILITIES (12 accounts)
        # Current Liabilities
        {'code': '2001', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2050', 'name': 'Credit Cards Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2100', 'name': 'Accrued Expenses', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2150', 'name': 'Sales Tax / VAT Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2200', 'name': 'Payroll Liabilities (PAYE, EPF/ETF, etc.)', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2250', 'name': 'Income Tax Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2300', 'name': 'Other Taxes & Levies Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2350', 'name': 'Customer Deposits / Deferred Revenue', 'type': 'liability', 'subtype': 'current_liability'},
        
        # Interest-Bearing Debt
        {'code': '2400', 'name': 'Loan – Current Portion', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2450', 'name': 'Loan – Long-Term', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2500', 'name': 'Lease Liabilities', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2550', 'name': 'Shareholder / Director Loans', 'type': 'liability', 'subtype': 'long_term_liability'},
        
        # EQUITY (5 accounts)
        {'code': '3001', 'name': 'Share / Owner Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3002', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'retained_earnings'},
        {'code': '3005', 'name': 'Additional Paid-In Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3010', 'name': 'Dividends / Owner Draws', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3020', 'name': 'Treasury Shares (contra-equity)', 'type': 'equity', 'subtype': 'owner_equity'},
        
        # COST OF GOODS SOLD (3 accounts)
        {'code': '3100', 'name': 'COGS – Materials / Merchandise', 'type': 'cost_of_goods_sold', 'subtype': 'cost_of_goods_sold'},
        {'code': '3200', 'name': 'COGS – Direct Labour', 'type': 'cost_of_goods_sold', 'subtype': 'cost_of_goods_sold'},
        {'code': '3300', 'name': 'COGS – Freight In & Import Duty', 'type': 'cost_of_goods_sold', 'subtype': 'cost_of_goods_sold'},
        
        # REVENUE (7 accounts)
        {'code': '4001', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4002', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4003', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4100', 'name': 'Subscription / Recurring Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4200', 'name': 'Interest Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        {'code': '4300', 'name': 'Sales Returns & Allowances (contra-revenue)', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4900', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        
        # EXPENSES (24 accounts)
        {'code': '5001', 'name': 'Operating Expenses – General', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5002', 'name': 'Meals & Entertainment', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5003', 'name': 'Transport & Travel', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5004', 'name': 'Office Supplies', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5005', 'name': 'Professional Services', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5006', 'name': 'Marketing & Advertising', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5007', 'name': 'Utilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5008', 'name': 'Rent & Facilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5009', 'name': 'Insurance', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5010', 'name': 'Software & SaaS', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5011', 'name': 'Bank & Merchant Fees', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5012', 'name': 'Depreciation Expense', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5013', 'name': 'Payroll – Wages & Salaries', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5014', 'name': 'Employee Benefits (EPF/ETF employer share, health)', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5015', 'name': 'Training & Development', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5016', 'name': 'Legal & Accounting', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5020', 'name': 'Subscriptions & Memberships', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5030', 'name': 'Repairs & Maintenance', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5040', 'name': 'Telephone & Internet', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5050', 'name': 'Bad Debt Expense', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5060', 'name': 'Interest Expense', 'type': 'expense', 'subtype': 'other_expense'},
        {'code': '5080', 'name': 'Licences, Permits & Government Fees', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5900', 'name': 'Other Expenses / Miscellaneous', 'type': 'expense', 'subtype': 'other_expense'},
    ]

def upgrade_organization_accounts(org_id, org_name):
    """Upgrade an organization to the comprehensive chart of accounts."""
    
    comprehensive_accounts = get_comprehensive_accounts()
    
    # Get existing accounts
    existing_accounts = Account.query.filter_by(organization_id=org_id).all()
    existing_codes = {acc.account_code for acc in existing_accounts}
    
    new_accounts_count = 0
    updated_accounts_count = 0
    
    for account_data in comprehensive_accounts:
        existing_account = Account.query.filter_by(
            organization_id=org_id,
            account_code=account_data['code']
        ).first()
        
        if existing_account:
            # Update existing account if name differs
            if existing_account.account_name != account_data['name']:
                existing_account.account_name = account_data['name']
                existing_account.updated_at = datetime.utcnow()
                updated_accounts_count += 1
        else:
            # Create new account
            account = Account(
                organization_id=org_id,
                account_code=account_data['code'],
                account_name=account_data['name'],
                account_type=account_data['type'],
                account_subtype=account_data['subtype'],
                is_active=True,
                description=f"Default {account_data['type']} account",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.session.add(account)
            new_accounts_count += 1
    
    return new_accounts_count, updated_accounts_count

def upgrade_all_organizations():
    """Upgrade all organizations to the comprehensive chart of accounts."""
    
    with app.app_context():
        try:
            # Get all organizations
            organizations = Organization.query.all()
            
            total_new = 0
            total_updated = 0
            upgraded_orgs = 0
            
            print(f"🔄 Upgrading {len(organizations)} organizations to 74-account structure...")
            
            for org in organizations:
                new_count, updated_count = upgrade_organization_accounts(org.id, org.name)
                
                if new_count > 0 or updated_count > 0:
                    print(f"  ✅ {org.name}: +{new_count} new, ~{updated_count} updated")
                    total_new += new_count
                    total_updated += updated_count
                    upgraded_orgs += 1
                else:
                    print(f"  ✓ {org.name}: Already up to date")
            
            # Commit all changes
            db.session.commit()
            
            print(f"\n🎉 UPGRADE COMPLETE!")
            print(f"  📊 Organizations upgraded: {upgraded_orgs}/{len(organizations)}")
            print(f"  ➕ Total new accounts created: {total_new}")
            print(f"  🔄 Total accounts updated: {total_updated}")
            
            # Verification
            print(f"\n🔍 VERIFICATION:")
            sample_orgs = ['Personal Finances', 'Lanka Solar Services', 'My Online (Private) Limited']
            
            for org_name in sample_orgs:
                org = Organization.query.filter_by(name=org_name).first()
                if org:
                    account_count = Account.query.filter_by(organization_id=org.id).count()
                    print(f"  {org_name}: {account_count} accounts")
                    
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error upgrading organizations: {str(e)}")
            raise

if __name__ == "__main__":
    print("🚀 Starting comprehensive chart of accounts upgrade...")
    upgrade_all_organizations()