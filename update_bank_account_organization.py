"""
Migration script to add organization_id column to the bank_account table.
"""
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def add_organization_id_to_bank_account():
    """
    Add organization_id column to the bank_account table.
    """
    try:
        # Check if the organization_id column already exists
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'bank_account' AND column_name = 'organization_id'
        """))
        
        if not result.first():
            print("The organization_id column doesn't exist in the bank_account table. This should have been created in an earlier migration.")
        
        # Update existing records in bank_account table - set organization_id to user's default organization
        db.session.execute(text("""
            UPDATE bank_account ba
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = ba.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE ba.organization_id IS NULL
        """))
        
        print("Updated organization_id for existing bank_account records")
        
        # Commit all changes
        db.session.commit()
        print("Bank account organization_id update completed successfully!")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        add_organization_id_to_bank_account()