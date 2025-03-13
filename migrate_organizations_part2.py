"""
Migration script - Part 2: Update invoice tables with organization_id.
This will link invoice data records to the correct organizations.
"""
import logging
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app import db, app

def update_invoice_organization_id():
    """
    Update invoices to link records to the correct organizations
    based on the user's default organization.
    """
    try:
        print("Updating organization_id for invoice table...")
        
        # Check if invoice table has the organization_id column
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'invoice' AND column_name = 'organization_id'
        """))
        
        if not result.first():
            print("Error: invoice table does not have organization_id column!")
            return
            
        # Update invoices
        result = db.session.execute(text("""
            UPDATE invoice i
            SET organization_id = (
                SELECT ou.organization_id
                FROM organization_user ou
                WHERE ou.user_id = i.user_id AND ou.is_default = true
                LIMIT 1
            )
            WHERE i.organization_id IS NULL
        """))
        print(f"Updated organization_id for {result.rowcount} invoices")
        db.session.commit()
        
        print("Invoice data migration completed successfully!")
            
    except SQLAlchemyError as e:
        db.session.rollback()
        print(f"Error during migration: {str(e)}")
        logging.error(f"Error during migration: {str(e)}")
        raise

if __name__ == "__main__":
    with app.app_context():
        update_invoice_organization_id()