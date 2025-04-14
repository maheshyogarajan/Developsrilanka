"""
Migration script to add the logo_s3_key column to the Organization model.
This allows storing organization logos in AWS S3.
"""
from app import db, app
import logging

logger = logging.getLogger(__name__)

def add_logo_s3_key_column():
    """
    Add the logo_s3_key column to the Organization table.
    """
    # Connect to the database
    with app.app_context():
        try:
            # Add the column using SQLAlchemy Core
            conn = db.engine.connect()
            
            # Check if the column already exists first
            result = conn.execute(db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='organization' AND column_name='logo_s3_key'"
            ))
            column_exists = result.fetchone() is not None
            
            if column_exists:
                print("logo_s3_key column already exists in organization table.")
                return
                
            # Add the column using text SQL
            conn.execute(db.text("ALTER TABLE organization ADD COLUMN logo_s3_key VARCHAR(255)"))
            conn.commit()
            print("Successfully added logo_s3_key column to organization table.")
            
        except Exception as e:
            print(f"Error adding logo_s3_key column to organization table: {str(e)}")
            raise
        finally:
            conn.close()

if __name__ == "__main__":
    add_logo_s3_key_column()
    print("Migration completed successfully!")