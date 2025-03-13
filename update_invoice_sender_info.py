"""
Migration script to add sender's information fields to Invoice model and support email functionality.
"""
import logging
from app import app, db
from sqlalchemy import text
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)

def update_invoice_model():
    """
    Add sender's information fields and email tracking to the invoice table.
    """
    try:
        with app.app_context():
            # Add sender information columns
            db.session.execute(text('''
                ALTER TABLE invoice 
                ADD COLUMN IF NOT EXISTS sender_name VARCHAR(255),
                ADD COLUMN IF NOT EXISTS sender_company VARCHAR(255),
                ADD COLUMN IF NOT EXISTS sender_address TEXT,
                ADD COLUMN IF NOT EXISTS sender_phone VARCHAR(50),
                ADD COLUMN IF NOT EXISTS sender_email VARCHAR(255),
                ADD COLUMN IF NOT EXISTS sender_tax_registration VARCHAR(100),
                ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMP
            '''))
            
            # Commit changes
            db.session.commit()
            
            logging.info("✅ Successfully added sender information fields to invoice table")
            
            # Auto-fill sender information from user data where appropriate
            db.session.execute(text('''
                UPDATE invoice i
                SET sender_name = u.name,
                    sender_email = u.email
                FROM "user" u
                WHERE i.user_id = u.id
                AND i.sender_name IS NULL
            '''))
            
            # Commit changes
            db.session.commit()
            
            logging.info("✅ Pre-filled sender name and email from user data where available")
            
            return True
    except Exception as e:
        logging.error(f"❌ Failed to update invoice model: {str(e)}")
        return False

if __name__ == "__main__":
    update_invoice_model()