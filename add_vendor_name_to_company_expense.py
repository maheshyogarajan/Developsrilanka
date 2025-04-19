"""
Migration script to add the vendor_name column to the company_expense table.
"""
import logging
import os
import sys
from datetime import datetime

# Add the current directory to the python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app import db
from sqlalchemy import text, Table, MetaData, Column, String

def add_vendor_name_column():
    """
    Add the vendor_name column to the company_expense table.
    """
    try:
        # Use raw SQL to add the column
        with db.engine.connect() as conn:
            # Check if the column already exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'company_expense' AND column_name = 'vendor_name'
            """))
            
            # If column already exists, skip
            if result.fetchone():
                logging.info("vendor_name column already exists in company_expense table.")
                return
            
            # Add the column
            conn.execute(text("""
                ALTER TABLE company_expense
                ADD COLUMN vendor_name VARCHAR(255)
            """))
            
            # Try to populate vendor_name from receipt data (if possible)
            conn.execute(text("""
                UPDATE company_expense ce
                SET vendor_name = r.vendor_name
                FROM receipt r
                WHERE ce.receipt_id = r.id AND r.vendor_name IS NOT NULL
            """))
            
            # Commit the transaction
            conn.commit()
            
            logging.info("Successfully added vendor_name column to company_expense table.")
            
    except Exception as e:
        logging.error(f"Error adding vendor_name column: {str(e)}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from app import app
    with app.app_context():
        add_vendor_name_column()
        print("Migration completed successfully.")