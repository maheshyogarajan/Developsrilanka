"""
Add thumbnail_s3_key column to the Receipt model.

This script adds a new column thumbnail_s3_key to the receipt table
to store references to thumbnail images in S3.
"""

import logging
from app import app, db
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """Add thumbnail_s3_key column to receipt table."""
    try:
        logger.info("Starting migration: Adding thumbnail_s3_key column to receipt table")
        
        with app.app_context():
            # Check if the column already exists
            inspector = db.inspect(db.engine)
            columns = [col['name'] for col in inspector.get_columns('receipt')]
            logger.info(f"Current columns in receipt table: {columns}")
            
            if 'thumbnail_s3_key' not in columns:
                # Add the column using the current SQLAlchemy approach
                logger.info("Column does not exist, attempting to add it now...")
                with db.engine.connect() as conn:
                    try:
                        conn.execute(text("ALTER TABLE receipt ADD COLUMN thumbnail_s3_key VARCHAR(255)"))
                        conn.commit()
                        logger.info("Successfully added thumbnail_s3_key column to receipt table")
                    except Exception as sql_error:
                        logger.error(f"SQL Error: {str(sql_error)}")
                        raise
            else:
                logger.info("Column thumbnail_s3_key already exists in receipt table")
            
            # Verify column was added
            inspector = db.inspect(db.engine)
            updated_columns = [col['name'] for col in inspector.get_columns('receipt')]
            logger.info(f"Updated columns in receipt table: {updated_columns}")
            
            if 'thumbnail_s3_key' in updated_columns:
                logger.info("✅ Verification successful: thumbnail_s3_key column exists")
            else:
                logger.error("❌ Verification failed: thumbnail_s3_key column still does not exist")
                
        return True
    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

if __name__ == '__main__':
    run_migration()