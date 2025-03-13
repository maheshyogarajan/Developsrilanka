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
            accounts = db.session.execute(db.text(
                'SELECT ba.id, ou.organization_id FROM bank_account ba '
                'JOIN user u ON ba.user_id = u.id '
                'JOIN organization_user ou ON u.id = ou.user_id '
                'WHERE ba.organization_id IS NULL AND ou.is_default = true'
            )).fetchall()
            
            # Update the accounts
            for account in accounts:
                db.session.execute(db.text(
                    'UPDATE bank_account SET organization_id = :org_id WHERE id = :account_id'
                ), {'org_id': account.organization_id, 'account_id': account.id})
            
            db.session.commit()
            logger.info(f"Updated {len(accounts)} bank accounts with their default organization.")
            
            return True
            
    except SQLAlchemyError as e:
        logger.error(f"Error adding organization_id to bank_account: {str(e)}")
        db.session.rollback()
        return False

if __name__ == "__main__":
    add_organization_id_to_bank_account()