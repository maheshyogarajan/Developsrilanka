"""
Migration: Add enhanced tax deductibility fields to ReceiptItem
Adds: deductibility_percentage, tax_law_reference, classification_confidence, deduction_notes
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)

def upgrade():
    """Add new columns to receipt_item table"""
    with app.app_context():
        try:
            logging.info("Starting migration: Adding tax deductibility fields to receipt_item table")
            
            # Add columns one by one with proper defaults
            migrations = [
                ("deductibility_percentage", "ALTER TABLE receipt_item ADD COLUMN IF NOT EXISTS deductibility_percentage INTEGER DEFAULT NULL"),
                ("tax_law_reference", "ALTER TABLE receipt_item ADD COLUMN IF NOT EXISTS tax_law_reference VARCHAR(500) DEFAULT NULL"),
                ("classification_confidence", "ALTER TABLE receipt_item ADD COLUMN IF NOT EXISTS classification_confidence FLOAT DEFAULT NULL"),
                ("deduction_notes", "ALTER TABLE receipt_item ADD COLUMN IF NOT EXISTS deduction_notes TEXT DEFAULT NULL")
            ]
            
            for field_name, sql in migrations:
                try:
                    logging.info(f"Adding column: {field_name}")
                    db.session.execute(text(sql))
                    db.session.commit()
                    logging.info(f"✓ Successfully added column: {field_name}")
                except Exception as e:
                    db.session.rollback()
                    logging.warning(f"Column {field_name} may already exist or error occurred: {str(e)}")
            
            logging.info("Migration completed successfully")
            return True
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Migration failed: {str(e)}")
            return False

def downgrade():
    """Remove the added columns (for rollback)"""
    with app.app_context():
        try:
            logging.info("Starting rollback: Removing tax deductibility fields from receipt_item table")
            
            migrations = [
                "ALTER TABLE receipt_item DROP COLUMN IF EXISTS deductibility_percentage",
                "ALTER TABLE receipt_item DROP COLUMN IF EXISTS tax_law_reference",
                "ALTER TABLE receipt_item DROP COLUMN IF EXISTS classification_confidence",
                "ALTER TABLE receipt_item DROP COLUMN IF EXISTS deduction_notes"
            ]
            
            for sql in migrations:
                db.session.execute(text(sql))
            
            db.session.commit()
            logging.info("Rollback completed successfully")
            return True
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"Rollback failed: {str(e)}")
            return False

if __name__ == "__main__":
    success = upgrade()
    if success:
        print("✓ Migration completed successfully")
    else:
        print("✗ Migration failed")
