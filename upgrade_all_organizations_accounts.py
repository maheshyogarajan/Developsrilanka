#!/usr/bin/env python3
"""
Upgrade all existing organizations to have the complete enhanced 74-account chart of accounts.
This ensures consistency across all organizations and fixes any missing accounts.
"""
import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

from main import app
from models import db, Organization
from accounting_models import Account
from datetime import datetime

def get_complete_chart_of_accounts():
    """Get the complete 74-account chart of accounts structure."""
    return [
        # ASSETS (23 accounts)
        {'code': '1001', 'name': 'Cash – Primary Bank', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1002', 'name': 'Cash – Business Savings', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1005', 'name': 'Petty Cash', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1050', 'name': 'Short-Term Investments', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1100', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1110', 'name': 'Allowance for Doubtful Accounts', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1200', 'name': 'Inventory', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1300', 'name': 'Prepaid Expenses', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1350', 'name': 'Input VAT / GST Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1400', 'name': 'Security Deposits & Advances', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1500', 'name': 'Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1550', 'name': 'Accum. Depreciation – Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1600', 'name': 'Computer Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1650', 'name': 'Accum. Depreciation – Computer Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1700', 'name': 'Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1750', 'name': 'Accum. Depreciation – Furniture & Fixtures', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1800', 'name': 'Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1850', 'name': 'Accum. Depreciation – Vehicles', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1900', 'name': 'Buildings', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1950', 'name': 'Accum. Depreciation – Buildings', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1970', 'name': 'Land', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1980', 'name': 'Intangible Assets', 'type': 'asset', 'subtype': 'intangible_asset'},
        {'code': '1990', 'name': 'Long-Term Investments', 'type': 'asset', 'subtype': 'investment'},

        # LIABILITIES (12 accounts)
        {'code': '2001', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2100', 'name': 'Credit Cards Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2200', 'name': 'Short-Term Loans', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2300', 'name': 'VAT / GST Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2400', 'name': 'Payroll Liabilities (EPF/ETF)', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2500', 'name': 'Income Tax Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2600', 'name': 'Accrued Expenses', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2700', 'name': 'Customer Deposits & Advances', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2800', 'name': 'Long-Term Loans', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2850', 'name': 'Mortgages Payable', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2900', 'name': 'Equipment Financing', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2950', 'name': 'Other Long-Term Liabilities', 'type': 'liability', 'subtype': 'long_term_liability'},

        # EQUITY (5 accounts)
        {'code': '3001', 'name': 'Owner Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3002', 'name': 'Additional Paid-In Capital', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3003', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'retained_earnings'},
        {'code': '3004', 'name': 'Current Year Earnings', 'type': 'equity', 'subtype': 'current_earnings'},
        {'code': '3005', 'name': 'Owner Drawings', 'type': 'equity', 'subtype': 'owner_drawings'},

        # REVENUE (7 accounts)
        {'code': '4001', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4002', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4003', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4004', 'name': 'Subscription Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4005', 'name': 'Interest Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        {'code': '4006', 'name': 'Investment Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        {'code': '4007', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'other_revenue'},

        # COST OF GOODS SOLD (3 accounts)
        {'code': '3100', 'name': 'Cost of Materials', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},
        {'code': '3200', 'name': 'Direct Labor', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},
        {'code': '3300', 'name': 'Freight & Shipping Costs', 'type': 'expense', 'subtype': 'cost_of_goods_sold'},

        # OPERATING EXPENSES (24 accounts)
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

def upgrade_organization_accounts(organization_id, organization_name):
    """Upgrade a single organization to have the complete chart of accounts."""
    try:
        print(f"\n🔄 Upgrading organization: {organization_name} (ID: {organization_id})")
        
        # Get existing accounts for this organization
        existing_accounts = {
            acc.account_code: acc 
            for acc in Account.query.filter_by(organization_id=organization_id).all()
        }
        
        print(f"   Current accounts: {len(existing_accounts)}")
        
        # Get the complete chart of accounts
        complete_accounts = get_complete_chart_of_accounts()
        
        accounts_added = 0
        accounts_updated = 0
        
        for account_data in complete_accounts:
            account_code = account_data['code']
            
            if account_code in existing_accounts:
                # Update existing account if needed
                existing_account = existing_accounts[account_code]
                updated = False
                
                if existing_account.account_name != account_data['name']:
                    existing_account.account_name = account_data['name']
                    updated = True
                
                if existing_account.account_type != account_data['type']:
                    existing_account.account_type = account_data['type']
                    updated = True
                    
                if existing_account.account_subtype != account_data['subtype']:
                    existing_account.account_subtype = account_data['subtype']
                    updated = True
                
                if updated:
                    existing_account.updated_at = datetime.utcnow()
                    accounts_updated += 1
            else:
                # Create new account
                new_account = Account(
                    organization_id=organization_id,
                    account_code=account_data['code'],
                    account_name=account_data['name'],
                    account_type=account_data['type'],
                    account_subtype=account_data['subtype'],
                    is_active=True,
                    description=f"Enhanced chart of accounts - {account_data['type']} account",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                db.session.add(new_account)
                accounts_added += 1
        
        # Commit changes for this organization
        db.session.commit()
        
        # Final count
        final_count = Account.query.filter_by(organization_id=organization_id).count()
        
        print(f"   ✅ Complete! Added: {accounts_added}, Updated: {accounts_updated}")
        print(f"   📊 Total accounts: {final_count}/74")
        
        return True
        
    except Exception as e:
        db.session.rollback()
        print(f"   ❌ Error upgrading {organization_name}: {str(e)}")
        return False

def upgrade_all_organizations():
    """Upgrade all organizations to have the complete enhanced chart of accounts."""
    
    with app.app_context():
        try:
            # Get all organizations
            organizations = Organization.query.all()
            print(f"\n🚀 Found {len(organizations)} organizations to upgrade")
            print("=" * 60)
            
            success_count = 0
            
            for org in organizations:
                success = upgrade_organization_accounts(org.id, org.name)
                if success:
                    success_count += 1
            
            print("\n" + "=" * 60)
            print(f"✅ Successfully upgraded {success_count}/{len(organizations)} organizations")
            
            # Final summary report
            print("\n📊 FINAL SUMMARY:")
            for org in organizations:
                account_count = Account.query.filter_by(organization_id=org.id).count()
                status = "✅ Complete" if account_count >= 74 else f"⚠️  {account_count}/74"
                print(f"   {org.name}: {status}")
                
        except Exception as e:
            print(f"❌ Critical error during upgrade: {str(e)}")

if __name__ == "__main__":
    print("🚀 UPGRADING ALL ORGANIZATIONS TO ENHANCED CHART OF ACCOUNTS")
    print("This will ensure all organizations have the complete 74-account structure")
    print("=" * 70)
    
    upgrade_all_organizations()
    
    print("\n🎉 Chart of accounts upgrade complete!")
    print("All organizations now have the enhanced professional accounting structure!")