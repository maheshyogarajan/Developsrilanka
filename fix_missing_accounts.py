"""
Fix missing accounts for specific organizations.
"""
import sys
import os
sys.path.insert(0, os.getcwd())

from main import app
from models import db, Organization
from accounting_models import Account
from datetime import datetime

def create_accounts_for_organization(org_id, org_name):
    """Create default accounts for a specific organization."""
    
    default_accounts = [
        # Assets
        {'code': '1001', 'name': 'Cash - Primary Bank Account', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1002', 'name': 'Cash - Business Savings', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1100', 'name': 'Accounts Receivable', 'type': 'asset', 'subtype': 'current_asset'},
        {'code': '1500', 'name': 'Office Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        {'code': '1600', 'name': 'Computer Equipment', 'type': 'asset', 'subtype': 'fixed_asset'},
        
        # Liabilities
        {'code': '2001', 'name': 'Accounts Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2100', 'name': 'Credit Cards Payable', 'type': 'liability', 'subtype': 'current_liability'},
        {'code': '2200', 'name': 'Business Loans', 'type': 'liability', 'subtype': 'long_term_liability'},
        {'code': '2300', 'name': 'Tax Payable', 'type': 'liability', 'subtype': 'current_liability'},
        
        # Equity
        {'code': '3001', 'name': 'Owner Equity', 'type': 'equity', 'subtype': 'owner_equity'},
        {'code': '3002', 'name': 'Retained Earnings', 'type': 'equity', 'subtype': 'retained_earnings'},
        
        # Revenue
        {'code': '4001', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4002', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4003', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4900', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        
        # Expenses
        {'code': '5001', 'name': 'Operating Expenses', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5002', 'name': 'Meals and Entertainment', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5003', 'name': 'Transport and Travel', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5004', 'name': 'Office Supplies', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5005', 'name': 'Professional Services', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5006', 'name': 'Marketing and Advertising', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5007', 'name': 'Utilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5008', 'name': 'Rent and Facilities', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5009', 'name': 'Insurance', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5010', 'name': 'Software and Technology', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5011', 'name': 'Bank Fees', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5012', 'name': 'Depreciation Expense', 'type': 'expense', 'subtype': 'operating_expense'},
        {'code': '5900', 'name': 'Other Expenses', 'type': 'expense', 'subtype': 'other_expense'},
    ]
    
    print(f"Creating {len(default_accounts)} accounts for {org_name} (ID: {org_id})...")
    
    for account_data in default_accounts:
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
    
    return len(default_accounts)

def fix_missing_accounts():
    """Fix accounts for key organizations."""
    
    with app.app_context():
        try:
            # Key organizations to fix
            key_orgs = [
                'Personal Finances',
                'Lanka Solar Services', 
                'My Online (Private) Limited',
                'Backpack Lanka (Private) Limited',
                'RENA'
            ]
            
            fixed_count = 0
            
            for org_name in key_orgs:
                org = Organization.query.filter_by(name=org_name).first()
                
                if org:
                    # Check if it has accounts
                    existing_count = Account.query.filter_by(organization_id=org.id).count()
                    
                    if existing_count == 0:
                        accounts_created = create_accounts_for_organization(org.id, org_name)
                        print(f"✅ Created {accounts_created} accounts for {org_name}")
                        fixed_count += 1
                    else:
                        print(f"✓ {org_name} already has {existing_count} accounts")
                else:
                    print(f"❌ Organization '{org_name}' not found")
            
            # Commit all changes
            db.session.commit()
            print(f"\n🎉 Successfully fixed {fixed_count} organizations!")
            
            # Verify the fix
            print("\n🔍 VERIFICATION:")
            for org_name in key_orgs:
                org = Organization.query.filter_by(name=org_name).first()
                if org:
                    account_count = Account.query.filter_by(organization_id=org.id).count()
                    print(f"  {org_name}: {account_count} accounts")
                    
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error fixing accounts: {str(e)}")
            raise

if __name__ == "__main__":
    print("🔧 Fixing missing chart of accounts...")
    fix_missing_accounts()