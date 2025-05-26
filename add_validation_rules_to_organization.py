"""
Add validation_rules column to Organization table
This enables Phase 2B organization-specific rule customization.
"""

from app import app, db
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_validation_rules_column():
    """Add validation_rules column to organization table."""
    try:
        with app.app_context():
            logger.info("Adding validation_rules column to organization table...")
            
            # Check if column already exists
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'organization' 
                AND column_name = 'validation_rules'
            """))
            
            if result.fetchone():
                logger.info("validation_rules column already exists")
                return True
            
            # Add the column
            db.session.execute(text("""
                ALTER TABLE organization 
                ADD COLUMN validation_rules TEXT
            """))
            
            # Initialize with default rules for existing organizations
            default_rules = """{
                "account_number_detection": {
                    "enabled": true,
                    "patterns": ["1000120800", "4567891234"]
                },
                "amount_thresholds": {
                    "enabled": true,
                    "min_amount": 0.01,
                    "max_amount": 1000000000
                },
                "summary_row_detection": {
                    "enabled": true,
                    "patterns": ["TOTAL DEPOSITS", "CLOSING BALANCE", "OPENING BALANCE", "BALANCE BROUGHT FORWARD", "BALANCE B/F", "TOTAL WITHDRAWALS"]
                },
                "date_pattern_exclusion": {
                    "enabled": true,
                    "patterns": ["25/26", "26/27", "24/25", "23/24"]
                }
            }"""
            
            # Set default rules for existing organizations
            db.session.execute(text("""
                UPDATE organization 
                SET validation_rules = :default_rules 
                WHERE validation_rules IS NULL
            """), {'default_rules': default_rules})
            
            db.session.commit()
            logger.info("✅ Successfully added validation_rules column to organization table")
            logger.info("✅ Initialized default rules for existing organizations")
            return True
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Error adding validation_rules column: {str(e)}")
        return False

if __name__ == "__main__":
    success = add_validation_rules_column()
    if success:
        print("✅ Migration completed successfully!")
    else:
        print("❌ Migration failed!")