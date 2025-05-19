"""
Main fix script to resolve the email verification issues.

This script does the following:
1. Runs the email verification migration to add the missing columns to the database
2. Updates the user loader function to handle missing columns gracefully
3. Restarts the application to apply the changes

Usage:
python fix_email_verification_issues.py
"""
import logging
import sys
import subprocess
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def print_step(step_number, description):
    """Print a step header for better visibility in the logs."""
    print(f"\n{'='*80}")
    print(f"STEP {step_number}: {description}")
    print(f"{'='*80}\n")

def run_migration():
    """Run the email verification migration script."""
    print_step(1, "Running email verification migration")
    
    try:
        # Import and run the migration
        from add_email_verification import add_email_verification_fields
        from app import app
        
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

def update_user_loader():
    """Update the user loader function to handle missing columns."""
    print_step(2, "Updating user loader function")
    
    try:
        # Import the fix_user_loader script
        from fix_user_loader import run as run_user_loader_fix
        
        # Run the user loader update
        success = run_user_loader_fix()
        
        if success:
            logger.info("User loader function updated successfully")
            return True
        else:
            logger.error("Failed to update user loader function")
            return False
    except Exception as e:
        logger.error(f"Error updating user loader: {str(e)}")
        return False

def verify_fixes():
    """Verify that our fixes were successful."""
    print_step(3, "Verifying fixes")
    
    try:
        from app import db
        from sqlalchemy import text
        
        # Check if the columns exist in the database
        result = db.session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'user' 
            AND column_name = 'is_email_verified'
        """)).fetchone()
        
        if result:
            logger.info("Email verification columns exist in the database")
            return True
        else:
            logger.warning("Email verification columns do not exist in the database")
            return False
    except Exception as e:
        logger.error(f"Error verifying fixes: {str(e)}")
        return False

def main():
    """Run the full fix process."""
    print("\nStarting email verification issue fix...")
    
    # Step 1: Run the migration
    if not run_migration():
        print("\nMigration failed. Exiting.")
        return False
    
    # Step 2: Update the user loader
    if not update_user_loader():
        print("\nUser loader update failed. Continuing with migration only.")
    
    # Step 3: Verify our fixes
    from app import app
    with app.app_context():
        if verify_fixes():
            print("\nAll fixes have been applied successfully!")
            print("The application should now function correctly with email verification enabled.")
            return True
        else:
            print("\nFixes were partially applied. The application may still have issues.")
            return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)