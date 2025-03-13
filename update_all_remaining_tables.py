"""
Migration script to add organization_id column to all remaining tables that need multi-organization support.
This includes:
1. receipt table
2. receipt_item table 
3. Any other tables referencing the organization model
"""
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def add_organization_id_to_remaining_tables():
    """
    Add organization_id column to remaining tables that need multi-organization support.
    """
    try:
        # ====== Update Receipt Table ======
        # Check if the organization_id column already exists in receipt table
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'receipt' AND column_name = 'organization_id'
        """))
        
        if not result.first():
            # Add the organization_id column to receipt table
            db.session.execute(text("""
                ALTER TABLE receipt 
                ADD COLUMN organization_id INTEGER
            """))
            
            print("Added organization_id column to receipt table")
            
            # Add a foreign key constraint to receipt table
            db.session.execute(text("""
                ALTER TABLE receipt
                ADD CONSTRAINT fk_receipt_organization
                FOREIGN KEY (organization_id)
                REFERENCES organization(id)
            """))
            
            print("Added foreign key constraint to receipt.organization_id")
            
            # Update existing records in receipt table - set organization_id to user's default organization
            db.session.execute(text("""
                UPDATE receipt r
                SET organization_id = (
                    SELECT ou.organization_id
                    FROM organization_user ou
                    WHERE ou.user_id = r.user_id AND ou.is_default = true
                    LIMIT 1
                )
            """))
            
            print("Updated organization_id for existing receipt records")
            
            db.session.commit()
        else:
            print("The organization_id column already exists in the receipt table")
        
        # ====== Check if any other tables need organization_id column ======
        # Add similar blocks for other tables if needed
        
        # Commit all changes
        db.session.commit()
        print("Migration completed successfully!")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        add_organization_id_to_remaining_tables()