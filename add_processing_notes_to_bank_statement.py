#!/usr/bin/env python3
"""
Database migration: Add processing_notes column to bank_statement table
This fixes the audit trail crash by adding the missing field.
"""

from app import app, db
from enhanced_financial_models import BankStatement
import logging

logger = logging.getLogger(__name__)

def migrate_add_processing_notes():
    """Add processing_notes column to bank_statement table."""
    with app.app_context():
        try:
            # Check if column already exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('bank_statement')]
            
            if 'processing_notes' not in columns:
                logger.info("Adding processing_notes column to bank_statement table...")
                
                # Add column as nullable first (zero-downtime pattern)
                with db.engine.connect() as conn:
                    conn.execute(db.text('ALTER TABLE bank_statement ADD COLUMN processing_notes TEXT NULL'))
                    
                    # Initialize existing rows with empty string
                    conn.execute(db.text("UPDATE bank_statement SET processing_notes = '' WHERE processing_notes IS NULL"))
                    conn.commit()
                
                logger.info("Successfully added processing_notes column")
                return True
            else:
                logger.info("processing_notes column already exists")
                return True
                
        except Exception as e:
            logger.error(f"Failed to add processing_notes column: {e}")
            return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = migrate_add_processing_notes()
    if success:
        print("✅ Migration completed successfully")
    else:
        print("❌ Migration failed")
        exit(1)