"""
Script to run the email verification migration to add the missing columns to the User table.
This fixes the registration error caused by the schema mismatch.
"""
import logging
import sys
from app import app, db

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """
    Import and run the email verification migration script.
    """
    try:
        # Import the migration function
        from add_email_verification import add_email_verification_fields
        
        # Run the migration within app context
        with app.app_context():
            success = add_email_verification_fields()
            
            if success:
                logger.info("Email verification migration completed successfully")
                return True
            else:
                logger.error("Email verification migration failed")
                return False
    except Exception as e:
        logger.error(f"Error running migration: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting email verification migration...")
    success = run_migration()
    
    if success:
        print("Migration completed successfully. Registration should now work.")
        sys.exit(0)
    else:
        print("Migration failed. See logs for details.")
        sys.exit(1)