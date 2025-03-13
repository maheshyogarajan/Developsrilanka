"""
Script to update users with personal organizations and ensure data consistency.
This will:
1. Create personal organizations for users who don't have them
2. Set personal_organization_id for all users
3. Update user_income records to link to the correct organizations
"""
import logging
from app import app, db
from models import User, Organization, UserIncome, Receipt

def create_personal_organizations(batch_size=10):
    """
    Create personal organizations for users who don't have them in batches.
    
    Args:
        batch_size: Number of users to process in each batch
    """
    with app.app_context():
        total_count = 0
        
        # Process users in batches
        offset = 0
        while True:
            users = User.query.filter(User.personal_organization_id.is_(None)).limit(batch_size).offset(offset).all()
            if not users:
                break
                
            count = 0
            for user in users:
                # Create personal organization
                logging.info(f"Creating personal organization for user {user.id}")
                personal_org = Organization(
                    name=f"{user.name}'s Personal Finances",
                    is_personal=True,
                    tax_rate_type="personal",
                    email=user.email,
                    # Default personal tax settings
                    employment_tax_rate=24.0,
                    business_income_tax_rate=36.0,
                    investment_tax_rate=14.0,
                    consulting_tax_rate=15.0
                )
                db.session.add(personal_org)
                db.session.flush()  # Get the ID without committing yet
                
                # Link to user
                user.personal_organization_id = personal_org.id
                count += 1
                
            db.session.commit()
            total_count += count
            logging.info(f"Batch processed: Created {count} personal organizations")
            print(f"Batch processed: Created {count} personal organizations")
            
            # If we processed fewer users than the batch size, we're done
            if count < batch_size:
                break
                
            # Move to the next batch
            offset += batch_size
            
        logging.info(f"Total: Created {total_count} personal organizations")
        return total_count

def update_income_organization_links(batch_size=50):
    """
    Update user_income records to link to personal organizations in batches.
    
    Args:
        batch_size: Number of income records to process in each batch
    """
    with app.app_context():
        total_count = 0
        
        # Process income records in batches
        offset = 0
        while True:
            # Get income records with no organization
            incomes = UserIncome.query.filter(UserIncome.organization_id.is_(None)).limit(batch_size).offset(offset).all()
            if not incomes:
                break
                
            count = 0
            for income in incomes:
                user = User.query.get(income.user_id)
                if not user:
                    logging.warning(f"Income record {income.id} has no valid user {income.user_id}")
                    continue
                    
                # If user has personal organization, link income record to it
                if user.personal_organization_id:
                    income.organization_id = user.personal_organization_id
                    count += 1
                    
            db.session.commit()
            total_count += count
            logging.info(f"Batch processed: Updated {count} income records")
            print(f"Batch processed: Updated {count} income records")
            
            # If we processed fewer records than the batch size, we're done
            if len(incomes) < batch_size:
                break
                
            # Move to the next batch
            offset += batch_size
            
        logging.info(f"Total: Updated {total_count} income records with personal organization links")
        return total_count

def update_receipt_organization_links():
    """Update receipt records to link to personal organizations."""
    with app.app_context():
        # Get all receipts
        receipts = Receipt.query.all()
        count = 0
        
        for receipt in receipts:
            user = User.query.get(receipt.user_id)
            if not user:
                logging.warning(f"Receipt {receipt.id} has no valid user {receipt.user_id}")
                continue
                
            # If receipt has no organization or organization doesn't match user's personal org
            if not receipt.organization_id and user.personal_organization_id:
                receipt.organization_id = user.personal_organization_id
                count += 1
                
        db.session.commit()
        logging.info(f"Updated {count} receipt records with personal organization links")
        return count

if __name__ == "__main__":
    print("Creating personal organizations...")
    org_count = create_personal_organizations()
    print(f"Created {org_count} personal organizations")
    
    print("Updating income organization links...")
    income_count = update_income_organization_links()
    print(f"Updated {income_count} income records")
    
    print("Updating receipt organization links...")
    receipt_count = update_receipt_organization_links()
    print(f"Updated {receipt_count} receipt records")
    
    print("Database update complete!")