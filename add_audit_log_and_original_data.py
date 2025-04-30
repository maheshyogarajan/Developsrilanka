"""
Migration script to add the audit_log table and original extraction fields to the Receipt model.
This will allow tracking user corrections and original extraction data.
"""

import os
import sys
import logging
from datetime import datetime
from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, Float, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Database connection
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    logger.error('DATABASE_URL environment variable not set')
    sys.exit(1)

engine = create_engine(db_url)
Session = sessionmaker(bind=engine)
session = Session()

def add_audit_log_table():
    """
    Add the audit_log table to the database.
    """
    try:
        # Create audit_log table
        session.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            entity_type VARCHAR(50) NOT NULL,
            entity_id INTEGER NOT NULL,
            action VARCHAR(20) NOT NULL,
            changed_fields JSONB NOT NULL,
            user_id INTEGER REFERENCES "user"(id),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ip_address VARCHAR(50),
            user_agent TEXT
        );
        """))
        
        logger.info('Successfully created audit_log table')
        return True
    except Exception as e:
        logger.error(f'Error creating audit_log table: {str(e)}')
        session.rollback()
        return False

def add_original_extraction_fields():
    """
    Add original extraction fields to the Receipt table.
    """
    try:
        # Check if columns already exist
        columns_exist = session.execute(text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'receipt' AND column_name = 'original_extracted_text';
        """)).fetchone()
        
        if columns_exist:
            logger.info('Original extraction fields already exist in the Receipt table')
            return True
        
        # Add columns
        session.execute(text("""
        ALTER TABLE receipt 
        ADD COLUMN IF NOT EXISTS original_extracted_text TEXT,
        ADD COLUMN IF NOT EXISTS extraction_confidence FLOAT,
        ADD COLUMN IF NOT EXISTS extraction_model VARCHAR(100),
        ADD COLUMN IF NOT EXISTS correction_count INTEGER DEFAULT 0;
        """))
        
        logger.info('Successfully added original extraction fields to the Receipt table')
        return True
    except Exception as e:
        logger.error(f'Error adding original extraction fields: {str(e)}')
        session.rollback()
        return False

def run_migration():
    """
    Run all migration steps.
    """
    try:
        audit_log_success = add_audit_log_table()
        receipt_fields_success = add_original_extraction_fields()
        
        if audit_log_success and receipt_fields_success:
            session.commit()
            logger.info('Migration completed successfully')
            return True
        else:
            session.rollback()
            logger.error('Migration failed')
            return False
    except Exception as e:
        logger.error(f'Error during migration: {str(e)}')
        session.rollback()
        return False
    finally:
        session.close()

if __name__ == '__main__':
    success = run_migration()
    sys.exit(0 if success else 1)