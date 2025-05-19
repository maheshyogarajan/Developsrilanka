"""
Migration script to add email verification fields to the User model.
"""
import logging
from datetime import datetime
from app import app, db
from models import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_email_verification_fields():
    """
    Add email verification fields to the User table.
    """
    try:
        # Check if columns already exist to avoid errors
        with db.engine.connect() as conn:
            # Add is_email_verified column with default value
            conn.execute(db.text("""
                ALTER TABLE "user" 
                ADD COLUMN IF NOT EXISTS is_email_verified BOOLEAN DEFAULT FALSE
            """))
            
            # Add email_verification_token column
            conn.execute(db.text("""
                ALTER TABLE "user" 
                ADD COLUMN IF NOT EXISTS email_verification_token VARCHAR(100)
            """))
            
            # Add email_verification_salt column for improved security
            conn.execute(db.text("""
                ALTER TABLE "user" 
                ADD COLUMN IF NOT EXISTS email_verification_salt VARCHAR(100)
            """))
            
            # Add email_verification_sent_at column
            conn.execute(db.text("""
                ALTER TABLE "user" 
                ADD COLUMN IF NOT EXISTS email_verification_sent_at TIMESTAMP
            """))
            
            # Mark existing users as verified to avoid disruption
            conn.execute(db.text("""
                UPDATE "user" 
                SET is_email_verified = TRUE 
                WHERE is_email_verified IS NULL
            """))
            
            # Commit the transaction
            conn.commit()
            
        logger.info("Email verification fields added successfully")
        return True
    except Exception as e:
        logger.error(f"Error adding email verification fields: {str(e)}")
        return False

if __name__ == "__main__":
    with app.app_context():
        success = add_email_verification_fields()
        print(f"Migration {'successful' if success else 'failed'}")