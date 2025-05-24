"""
Diagnostic script to check why chart of accounts is empty.
"""
import sys
import os
sys.path.insert(0, os.getcwd())

from main import app
from models import db, Organization
from accounting_models import Account

def diagnose_accounts():
    """Check what accounts exist and why they're not showing."""
    
    with app.app_context():
        # Find Personal Finances organization
        personal_finances = Organization.query.filter_by(name='Personal Finances').first()
        
        if personal_finances:
            print(f"✅ Found Personal Finances organization (ID: {personal_finances.id})")
            
            # Check accounts for this organization
            accounts = Account.query.filter_by(organization_id=personal_finances.id).all()
            print(f"📊 Found {len(accounts)} accounts for Personal Finances")
            
            if accounts:
                print("\n📋 ACCOUNTS LIST:")
                for account in accounts[:10]:  # Show first 10
                    print(f"  {account.account_code} - {account.account_name} ({account.account_type})")
                if len(accounts) > 10:
                    print(f"  ... and {len(accounts) - 10} more accounts")
                    
                # Check active accounts
                active_accounts = Account.query.filter_by(
                    organization_id=personal_finances.id,
                    is_active=True
                ).all()
                print(f"\n✅ Active accounts: {len(active_accounts)}")
                
                # Group by type
                types = {}
                for account in active_accounts:
                    if account.account_type not in types:
                        types[account.account_type] = 0
                    types[account.account_type] += 1
                
                print("\n📊 ACCOUNTS BY TYPE:")
                for acc_type, count in types.items():
                    print(f"  {acc_type}: {count} accounts")
                    
            else:
                print("❌ No accounts found for Personal Finances organization")
        else:
            print("❌ Personal Finances organization not found")
            
            # Show all organizations
            all_orgs = Organization.query.limit(10).all()
            print(f"\n📋 Available organizations (first 10):")
            for org in all_orgs:
                account_count = Account.query.filter_by(organization_id=org.id).count()
                print(f"  {org.name} (ID: {org.id}) - {account_count} accounts")

if __name__ == "__main__":
    diagnose_accounts()