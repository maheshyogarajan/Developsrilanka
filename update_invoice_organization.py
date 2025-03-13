"""
Migration script to add organization_id column to the invoice table.
"""
import logging
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import app, db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_organization_id_to_invoice():
    """
    Add organization_id column to the invoice table.
    """
    try:
        with app.app_context():
            # Check if the column already exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'invoice' AND column_name = 'organization_id'
            """))
            
            if result.first():
                logger.info("Column organization_id already exists in invoice table.")
                return
            
            # Add the organization_id column
            logger.info("Adding organization_id column to invoice table...")
            db.session.execute(text("""
                ALTER TABLE invoice 
                ADD COLUMN organization_id INTEGER REFERENCES organization(id)
            """))
            
            # Update existing invoices to use the corresponding user's default organization
            logger.info("Updating existing invoices with default organization ID...")
            db.session.execute(text("""
                UPDATE invoice i
                SET organization_id = (
                    SELECT ou.organization_id
                    FROM organization_user ou
                    WHERE ou.user_id = i.user_id AND ou.is_default = true
                    LIMIT 1
                )
                WHERE i.organization_id IS NULL
            """))
            
            db.session.commit()
            logger.info("Successfully added organization_id to invoice table")
    
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Error updating invoice table: {str(e)}")
        raise

if __name__ == "__main__":
    add_organization_id_to_invoice()