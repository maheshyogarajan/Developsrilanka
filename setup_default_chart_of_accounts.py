"""
Setup default chart of accounts for all existing organizations.
This migration creates essential accounting accounts to fix the empty chart of accounts issue.
"""
import sys
import os

# Add the current directory to Python path
sys.path.insert(0, os.getcwd())

from main import app
from models import db, Organization
from accounting_models import Account
from datetime import datetime


def create_default_chart_of_accounts():
    """Create default chart of accounts for all organizations that don't have any accounts."""
    
    # Default chart of accounts structure
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
        
        # Revenue (mapped to existing business model)
        {'code': '4001', 'name': 'Service Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4002', 'name': 'Consulting Revenue', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4003', 'name': 'Product Sales', 'type': 'revenue', 'subtype': 'operating_revenue'},
        {'code': '4900', 'name': 'Other Income', 'type': 'revenue', 'subtype': 'other_revenue'},
        
        # Expenses (mapped to existing receipt categories)
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
    
    with app.app_context():
        try:
            # Get all organizations
            organizations = Organization.query.all()
            print(f"Found {len(organizations)} organizations")
            
            for org in organizations:
                # Check if organization already has accounts
                existing_accounts = Account.query.filter_by(organization_id=org.id).count()
                
                if existing_accounts == 0:
                    print(f"Creating chart of accounts for organization: {org.name} (ID: {org.id})")
                    
                    # Create all default accounts for this organization
                    accounts_created = 0
                    for account_data in default_accounts:
                        account = Account(
                            organization_id=org.id,
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
                        accounts_created += 1
                    
                    print(f"  → Created {accounts_created} accounts for {org.name}")
                else:
                    print(f"Organization {org.name} already has {existing_accounts} accounts, skipping")
            
            # Commit all changes
            db.session.commit()
            print("\n✅ Default chart of accounts setup completed successfully!")
            
            # Summary report
            print("\n📊 SUMMARY:")
            for org in organizations:
                account_count = Account.query.filter_by(organization_id=org.id).count()
                print(f"  {org.name}: {account_count} accounts")
                
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error setting up chart of accounts: {str(e)}")
            raise


def verify_account_setup():
    """Verify that all organizations now have proper chart of accounts."""
    
    with app.app_context():
        organizations = Organization.query.all()
        
        print("\n🔍 VERIFICATION REPORT:")
        print("=" * 50)
        
        all_good = True
        for org in organizations:
            accounts_by_type = {}
            accounts = Account.query.filter_by(organization_id=org.id).all()
            
            for account in accounts:
                account_type = account.account_type
                if account_type not in accounts_by_type:
                    accounts_by_type[account_type] = 0
                accounts_by_type[account_type] += 1
            
            print(f"\n{org.name} (ID: {org.id}):")
            if accounts_by_type:
                for account_type, count in sorted(accounts_by_type.items()):
                    print(f"  {account_type.capitalize()}: {count} accounts")
                print(f"  Total: {sum(accounts_by_type.values())} accounts")
            else:
                print("  ❌ NO ACCOUNTS FOUND")
                all_good = False
        
        if all_good:
            print("\n✅ All organizations have proper chart of accounts!")
        else:
            print("\n❌ Some organizations still missing accounts")
        
        return all_good


if __name__ == "__main__":
    print("🚀 Setting up default chart of accounts for all organizations...")
    print("=" * 60)
    
    create_default_chart_of_accounts()
    verify_account_setup()
    
    print("\n🎉 Chart of accounts setup complete!")
    print("You can now view accounts at: /accounts/chart-of-accounts")