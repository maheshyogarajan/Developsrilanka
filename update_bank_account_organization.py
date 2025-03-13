"""
Migration script to add organization_id column to the bank_account table.
"""
import logging
from app import app, db
from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.exc import SQLAlchemyError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_organization_id_to_bank_account():
    """
    Add organization_id column to the bank_account table.
    """
    try:
        with app.app_context():
            # Check if the column already exists
            inspector = db.inspect(db.engine)
            columns = [column['name'] for column in inspector.get_columns('bank_account')]
            
            if 'organization_id' not in columns:
                # Add the column
                logger.info("Adding organization_id column to bank_account table...")
                
                # Execute SQL to add the column
                db.session.execute(db.text(
                    'ALTER TABLE bank_account ADD COLUMN organization_id INTEGER REFERENCES organization(id)'
                ))
                
                db.session.commit()
                logger.info("Successfully added organization_id column to bank_account table.")
            else:
                logger.info("organization_id column already exists in bank_account table.")
                
            # Update existing bank accounts to associate with user's default organization
            logger.info("Updating existing bank accounts with default organization...")
            
            # Get all bank accounts without organization_id
            # Note: Using a different approach since we may not have the user table query working correctly
            logger.info("Gathering bank accounts without organization association...")
            accounts = []
            
            # First, get all accounts with NULL organization_id
            null_org_accounts = db.session.execute(db.text(
                'SELECT id, user_id FROM bank_account WHERE organization_id IS NULL'
            )).fetchall()
            
            # For each account, find the user's default organization
            for account in null_org_accounts:
                default_org = db.session.execute(db.text(
                    'SELECT organization_id FROM organization_user '
                    'WHERE user_id = :user_id AND is_default = true'
                ), {'user_id': account.user_id}).first()
                
                if default_org:
                    accounts.append({
                        'id': account.id,
                        'organization_id': default_org.organization_id
                    })
                else:
                    logger.warning(f"No default organization found for user ID {account.user_id}")
            
            logger.info(f"Found {len(accounts)} bank accounts to update")
            
            # Update the accounts
            for account in accounts:
                db.session.execute(db.text(
                    'UPDATE bank_account SET organization_id = :org_id WHERE id = :account_id'
                ), {'org_id': account['organization_id'], 'account_id': account['id']})
            
            db.session.commit()
            logger.info(f"Updated {len(accounts)} bank accounts with their default organization.")
            
            return True
            
    except SQLAlchemyError as e:
        logger.error(f"Error adding organization_id to bank_account: {str(e)}")
        db.session.rollback()
        return False

if __name__ == "__main__":
    add_organization_id_to_bank_account()